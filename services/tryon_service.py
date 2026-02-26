"""
Try-On Service — orchestrates the virtual try-on pipeline.

Accepts person + garment image URLs, submits to PiAPI Kling (ai_try_on),
polls for completion, and returns the result image URL.
"""

import io

import httpx
import structlog
from PIL import Image

from clients.piapi_client import PiAPIClient
from core.exceptions import APIError

# PiAPI enforces a 512px minimum on both dimensions
PIAPI_MIN_DIMENSION = 512

logger = structlog.get_logger(__name__)

VALID_GARMENT_TYPES = {"upper", "lower", "overall"}

_GARMENT_FIELD_MAP = {
    "upper": "upper_input",
    "lower": "lower_input",
    "overall": "dress_input",
}


class TryonService:
    """Orchestrates the Kling AI virtual try-on pipeline.

    A single instance can be shared across the application lifetime.
    """

    def __init__(self, piapi_client: PiAPIClient | None = None) -> None:
        self._piapi = piapi_client or PiAPIClient()

    async def run(
        self,
        user_id: int,
        person_image_url: str,
        garment_image_url: str,
        garment_type: str = "upper",
    ) -> str:
        """Run the virtual try-on pipeline end to end.

        Args:
            user_id: Caller's user ID (reserved for future usage tracking).
            person_image_url: Publicly accessible URL of the person/model image.
            garment_image_url: Publicly accessible URL of the garment image.
            garment_type: "upper", "lower", or "overall".

        Returns:
            Result image URL from PiAPI.

        Raises:
            ValueError: If garment_type is not one of the accepted values.
            APIError: If the PiAPI task fails or returns no result URL.
            TaskTimeoutError: If polling exceeds the maximum wait time (~5 min).
        """
        if garment_type not in VALID_GARMENT_TYPES:
            raise ValueError(
                f"Invalid garment_type '{garment_type}'. "
                f"Supported: {', '.join(sorted(VALID_GARMENT_TYPES))}"
            )

        garment_field = _GARMENT_FIELD_MAP[garment_type]

        log = logger.bind(user_id=user_id, garment_type=garment_type)
        log.info("tryon_start")

        await self._check_dimensions(person_image_url, "person")
        await self._check_dimensions(garment_image_url, "garment")

        input_payload = {
            "model_input": person_image_url,
            garment_field: garment_image_url,
            "batch_size": 1,
        }

        task_data = await self._piapi.create_and_poll(
            model="kling",
            task_type="ai_try_on",
            input_payload=input_payload,
            config={"service_mode": "public"},
        )

        result_url = self._extract_result_url(task_data)
        if not result_url:
            log.error("tryon_no_result_url", output=task_data.get("output"))
            raise APIError(
                "PiAPI returned a completed task but no result image URL was found."
            )

        log.info("tryon_complete", result_url=result_url)
        return result_url

    async def _check_dimensions(self, url: str, label: str) -> None:
        """Download image from URL and verify both dimensions are >= 512px.

        Raises:
            ValueError: If either dimension is below PIAPI_MIN_DIMENSION.
        """
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get(url)
                response.raise_for_status()
                image_bytes = response.content
        except httpx.HTTPError as exc:
            raise ValueError(f"Could not download {label} image from URL: {exc}") from exc

        img = Image.open(io.BytesIO(image_bytes))
        w, h = img.size
        if w < PIAPI_MIN_DIMENSION or h < PIAPI_MIN_DIMENSION:
            raise ValueError(
                f"{label.capitalize()} image is {w}×{h}px — "
                f"PiAPI requires at least {PIAPI_MIN_DIMENSION}px on both sides. "
                f"Please use a higher-resolution image."
            )

    def _extract_result_url(self, task_data: dict) -> str:
        """Extract result image URL from completed task data.

        PiAPI ai_try_on response shape:
          output.works[0].image.resource_without_watermark  (preferred)
          output.works[0].image.resource                    (fallback)
        """
        works = task_data.get("output", {}).get("works", [])
        if not works:
            return ""
        image = works[0].get("image", {})
        return image.get("resource_without_watermark") or image.get("resource", "")
