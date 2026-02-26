"""
Model Generator service — generates photorealistic human model images from a text prompt.

Uses Gemini 2.5 Flash via PiAPI. Generated images are saved locally to data/model/.
The output image path can be passed directly to TryOnService as the person_image input.

Usage:
    generator = ModelGenerator()
    file_path = await generator.generate(
        prompt="young Indian male, casual standing pose",
        aspect_ratio="2:3",
    )
    # file_path → "data/model/3f2a1b4c-....jpg"
"""

import uuid
from pathlib import Path

import httpx
import structlog

from clients.piapi_client import PiAPIClient
from core.exceptions import APIError

logger = structlog.get_logger(__name__)

# Local folder where generated model images are saved
MODEL_OUTPUT_DIR = Path("data/model")

# Fashion-context prefix prepended to every user prompt for consistent quality
PROMPT_PREFIX = (
    "photorealistic fashion model, full body, plain white studio background, "
    "professional fashion photography, high quality, sharp focus, "
)

VALID_ASPECT_RATIOS = {
    "21:9", "1:1", "4:3", "3:2", "2:3", "5:4", "4:5", "3:4", "16:9", "9:16"
}


class ModelGenerator:
    """Generates photorealistic human model images from a text prompt.

    Uses Gemini 2.5 Flash (via PiAPI) for image generation.
    Saves output images locally to data/model/{uuid}.jpg.
    """

    def __init__(self, piapi_client: PiAPIClient | None = None) -> None:
        self._client = piapi_client or PiAPIClient()

    async def generate(
        self,
        prompt: str,
        aspect_ratio: str = "2:3",
    ) -> tuple[str, str]:
        """Generate a human model image from a text prompt.

        Args:
            prompt: User's description of the model
                    (e.g. "young Indian male, casual standing pose").
            aspect_ratio: Image aspect ratio. Default "2:3" (portrait) is best
                          for full-body fashion shots.

        Returns:
            Tuple of (local_file_path, piapi_image_url).
            local_file_path is relative: "data/model/{uuid}.jpg"

        Raises:
            ValueError: If aspect_ratio is not supported.
            APIError: If image generation fails.
        """
        if aspect_ratio not in VALID_ASPECT_RATIOS:
            raise ValueError(
                f"Invalid aspect_ratio '{aspect_ratio}'. "
                f"Supported: {', '.join(sorted(VALID_ASPECT_RATIOS))}"
            )

        full_prompt = PROMPT_PREFIX + prompt.strip()
        log = logger.bind(prompt_preview=prompt[:60], aspect_ratio=aspect_ratio)
        log.info("model_generation_start")

        # 1. Call Gemini 2.5 Flash via PiAPI
        task_data = await self._client.create_and_poll(
            model="gemini",
            task_type="gemini-2.5-flash-image",
            input_payload={
                "prompt": full_prompt,
                "aspect_ratio": aspect_ratio,
                "output_format": "jpeg",
            },
        )

        # 2. Extract image URL from task output
        output = task_data.get("output", {})
        image_url: str = output.get("image_url") or (
            output.get("image_urls") or [None]
        )[0]

        if not image_url:
            raise APIError("PiAPI returned no image URL in task output.")

        log.info("model_generation_complete", image_url=image_url)

        # 3. Download image
        image_bytes = await self._download_image(image_url)

        # 4. Save locally
        file_path = await self._save_image(image_bytes)

        log.info("model_image_saved", file_path=file_path)
        return file_path, image_url

    async def _download_image(self, url: str) -> bytes:
        """Download image bytes from a URL."""
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.get(url)
                response.raise_for_status()
                return response.content
        except httpx.RequestError as exc:
            raise APIError(f"Failed to download generated image: {exc}") from exc

    async def _save_image(self, image_bytes: bytes) -> str:
        """Save image bytes to data/model/ and return the relative file path."""
        MODEL_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        filename = f"{uuid.uuid4()}.jpg"
        file_path = MODEL_OUTPUT_DIR / filename
        file_path.write_bytes(image_bytes)
        return str(file_path)
