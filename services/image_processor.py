"""
Image processing service for the fashion-ai bot.

Handles validation, resizing, format conversion, and background removal
for person and garment images before submission to the PiAPI try-on pipeline.

Usage:
    processor = ImageProcessor()
    await processor.validate(image_bytes, filename="garment.png")
    clean_bytes = await processor.preprocess(image_bytes)
    no_bg_bytes = await processor.remove_background(clean_bytes)
    b64_string = await processor.to_base64(no_bg_bytes)
"""

import asyncio
import base64
import io
from typing import TYPE_CHECKING

import magic
import structlog
from PIL import Image
from rembg import new_session, remove

from core.constants import (
    JPEG_QUALITY,
    MAX_IMAGE_BYTES,
    MAX_IMAGE_DIMENSION,
    MIN_IMAGE_DIMENSION,
    SUPPORTED_MIME_TYPES,
)
from core.exceptions import ImageValidationError

if TYPE_CHECKING:
    from rembg import Session

logger = structlog.get_logger(__name__)


class ImageProcessor:
    """Validates, normalizes, and processes images for the try-on pipeline.

    This class is stateless except for the lazily-initialized rembg session.
    A single instance can safely be shared across the lifetime of the
    application (or one Celery worker process).

    The rembg session is created once on first use and reused for all
    subsequent remove_background() calls, avoiding the ~170 MB ONNX
    model reload cost on every request.
    """

    def __init__(self) -> None:
        self._rembg_session: "Session | None" = None

    def _get_rembg_session(self) -> "Session":
        """Return the rembg U2Net session, initializing it on first call."""
        if self._rembg_session is None:
            logger.info("rembg_session_init", model="u2net")
            self._rembg_session = new_session("u2net")
        return self._rembg_session

    async def validate(self, image_bytes: bytes, filename: str) -> None:
        """Validate image bytes against size, mime type, and dimension constraints.

        Args:
            image_bytes: Raw bytes of the uploaded image.
            filename: Original filename (used only for logging — NOT for mime detection).

        Raises:
            ImageValidationError: If any validation check fails, with a
                user-friendly message describing the specific failure.
        """
        log = logger.bind(filename=filename)

        # 1. File size
        size_bytes = len(image_bytes)
        if size_bytes > MAX_IMAGE_BYTES:
            size_mb = size_bytes / (1024 * 1024)
            max_mb = MAX_IMAGE_BYTES / (1024 * 1024)
            log.warning("image_too_large", size_mb=round(size_mb, 1), max_mb=max_mb)
            raise ImageValidationError(
                f"Image is too large ({size_mb:.1f} MB). Maximum allowed size is {max_mb:.0f} MB."
            )

        # 2. Mime type — never trust the file extension
        try:
            mime_type: str = magic.from_buffer(image_bytes, mime=True)
        except Exception as exc:
            log.error("mime_detection_failed", error=str(exc))
            raise ImageValidationError(
                "Could not determine the image format. Please send a JPEG, PNG, or WebP image."
            ) from exc

        if mime_type not in SUPPORTED_MIME_TYPES:
            log.warning("unsupported_mime_type", mime_type=mime_type)
            supported = ", ".join(sorted(SUPPORTED_MIME_TYPES))
            raise ImageValidationError(
                f"Unsupported image format: {mime_type}. "
                f"Please send one of: {supported}."
            )

        # 3. Image dimensions
        try:
            with Image.open(io.BytesIO(image_bytes)) as img:
                width, height = img.size
        except Exception as exc:
            log.error("image_open_failed", error=str(exc))
            raise ImageValidationError(
                "The image file appears to be corrupted or unreadable. "
                "Please send a different image."
            ) from exc

        if width < MIN_IMAGE_DIMENSION or height < MIN_IMAGE_DIMENSION:
            log.warning(
                "image_too_small",
                width=width,
                height=height,
                min_dimension=MIN_IMAGE_DIMENSION,
            )
            raise ImageValidationError(
                f"Image is too small ({width}x{height} px). "
                f"Both dimensions must be at least {MIN_IMAGE_DIMENSION} px."
            )

        log.info(
            "image_validated",
            size_bytes=size_bytes,
            mime_type=mime_type,
            width=width,
            height=height,
        )

    async def preprocess(self, image_bytes: bytes) -> bytes:
        """Resize and normalize an image for API submission.

        Operations (in order):
        1. Resize: scale down longest edge to MAX_IMAGE_DIMENSION if needed.
           Images smaller than the limit are never upscaled.
        2. Color mode: convert to RGB (handles RGBA, palette, grayscale, CMYK).
           RGBA images are pasted onto a white background before conversion
           so transparent areas become white, not black.
        3. Encode as JPEG at JPEG_QUALITY=95.

        Args:
            image_bytes: Raw bytes of a validated image.

        Returns:
            JPEG-encoded bytes of the processed image.

        Raises:
            ImageValidationError: If the image cannot be opened or processed.
        """
        try:
            return await asyncio.to_thread(self._preprocess_sync, image_bytes)
        except ImageValidationError:
            raise
        except Exception as exc:
            logger.error("preprocess_failed", error=str(exc))
            raise ImageValidationError(
                "Failed to process the image. Please try sending a different image."
            ) from exc

    def _preprocess_sync(self, image_bytes: bytes) -> bytes:
        """Synchronous image preprocessing — called via asyncio.to_thread()."""
        with Image.open(io.BytesIO(image_bytes)) as img:
            original_size = img.size

            # Resize if longest edge exceeds the limit
            max_edge = max(img.width, img.height)
            if max_edge > MAX_IMAGE_DIMENSION:
                scale = MAX_IMAGE_DIMENSION / max_edge
                new_size = (int(img.width * scale), int(img.height * scale))
                img = img.resize(new_size, resample=Image.Resampling.LANCZOS)
                logger.info("image_resized", original_size=original_size, new_size=new_size)

            # Convert to RGB — JPEG cannot encode RGBA, palette, or grayscale directly.
            # For RGBA: paste onto white background so transparent areas stay white.
            if img.mode != "RGB":
                if img.mode == "RGBA":
                    background = Image.new("RGB", img.size, (255, 255, 255))
                    background.paste(img, mask=img.split()[3])  # split()[3] = alpha channel
                    img = background
                else:
                    img = img.convert("RGB")

            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)
            return buf.getvalue()

    async def remove_background(self, image_bytes: bytes) -> bytes:
        """Remove the background from a garment image using rembg U2Net.

        Use this on garment images only. Applying background removal to
        person images degrades try-on quality significantly.

        rembg is CPU-bound and synchronous; wrapped with asyncio.to_thread()
        so it does not block the event loop.

        Args:
            image_bytes: Raw bytes of a preprocessed garment image.

        Returns:
            PNG-encoded bytes with a transparent background (RGBA mode).

        Raises:
            ImageValidationError: If background removal fails.
        """
        try:
            return await asyncio.to_thread(self._remove_background_sync, image_bytes)
        except ImageValidationError:
            raise
        except Exception as exc:
            logger.error("remove_background_failed", error=str(exc))
            raise ImageValidationError(
                "Failed to remove the garment background. Please try a different image."
            ) from exc

    def _remove_background_sync(self, image_bytes: bytes) -> bytes:
        """Synchronous background removal — called via asyncio.to_thread()."""
        session = self._get_rembg_session()
        result_bytes: bytes = remove(image_bytes, session=session)
        logger.info("background_removed", output_size_bytes=len(result_bytes))
        return result_bytes

    async def to_base64(self, image_bytes: bytes) -> str:
        """Base64-encode image bytes for API upload.

        Returns plain base64 (no data URI prefix). PiAPI accepts base64-encoded
        images as an alternative to passing a URL.

        Args:
            image_bytes: Raw image bytes to encode.

        Returns:
            Base64-encoded string.
        """
        encoded = base64.b64encode(image_bytes).decode("utf-8")
        logger.debug("image_base64_encoded", output_length=len(encoded))
        return encoded
