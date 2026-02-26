"""
PiAPI HTTP client — async wrapper for task creation and polling.

Supports all PiAPI models (Kling, Gemini, etc.) via the same unified task API.

Usage:
    client = PiAPIClient()
    task_data = await client.create_and_poll(
        model="gemini",
        task_type="gemini-2.5-flash-image",
        input_payload={"prompt": "fashion model", "aspect_ratio": "2:3"},
    )
    image_url = task_data["output"]["image_url"]
"""

import asyncio

import httpx
import structlog

from core.config import settings
from core.exceptions import APIError, TaskTimeoutError

logger = structlog.get_logger(__name__)

PIAPI_BASE_URL = "https://api.piapi.ai/api/v1"

# Polling config
POLL_INTERVAL_START = 5.0   # seconds
POLL_INTERVAL_STEP  = 2.0   # increase per attempt
POLL_INTERVAL_MAX   = 15.0  # cap
POLL_MAX_ATTEMPTS   = 60    # ~5 minutes total

TERMINAL_STATUSES = {"completed", "failed"}


class PiAPIClient:
    """Async wrapper for the PiAPI unified task API.

    A single instance can be shared across the application lifetime.
    The underlying httpx.AsyncClient is created per-request to avoid
    event-loop ownership issues across Celery task boundaries.
    """

    def _headers(self) -> dict[str, str]:
        return {
            "X-API-Key": settings.PIAPI_API_KEY,
            "Content-Type": "application/json",
        }

    async def create_task(
        self,
        model: str,
        task_type: str,
        input_payload: dict,
    ) -> str:
        """Submit a new task to PiAPI and return the task_id.

        Args:
            model: Model identifier, e.g. "gemini" or "kling".
            task_type: Task type, e.g. "gemini-2.5-flash-image" or "virtual_try_on".
            input_payload: Model-specific input dict (prompt, aspect_ratio, etc.).

        Returns:
            task_id string from PiAPI.

        Raises:
            APIError: If the request fails or PiAPI returns a non-200 code.
        """
        payload = {
            "model": model,
            "task_type": task_type,
            "input": input_payload,
        }

        log = logger.bind(model=model, task_type=task_type)
        log.info("piapi_create_task")

        try:
            async with httpx.AsyncClient(timeout=120) as client:
                response = await client.post(
                    f"{PIAPI_BASE_URL}/task",
                    json=payload,
                    headers=self._headers(),
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            log.error("piapi_http_error", status_code=exc.response.status_code, body=exc.response.text)
            raise APIError(f"PiAPI request failed ({exc.response.status_code}): {exc.response.text}") from exc
        except httpx.RequestError as exc:
            log.error("piapi_request_error", error=str(exc))
            raise APIError(f"Failed to connect to PiAPI: {exc}") from exc

        data = response.json()
        if data.get("code") != 200:
            raise APIError(f"PiAPI error: {data.get('message', 'unknown error')}")

        task_id: str = data["data"]["task_id"]
        log.info("piapi_task_created", task_id=task_id)
        return task_id

    async def poll_task(
        self,
        task_id: str,
        max_attempts: int = POLL_MAX_ATTEMPTS,
    ) -> dict:
        """Poll a PiAPI task until it reaches a terminal status.

        Args:
            task_id: The task ID returned by create_task().
            max_attempts: Maximum number of poll attempts before giving up.

        Returns:
            The completed task data dict (contains output.image_url or output.video_url).

        Raises:
            APIError: If the task status is "Failed".
            TaskTimeoutError: If max_attempts is reached without completion.
        """
        log = logger.bind(task_id=task_id)
        elapsed = 0.0

        for attempt in range(1, max_attempts + 1):
            interval = min(
                POLL_INTERVAL_START + (attempt - 1) * POLL_INTERVAL_STEP,
                POLL_INTERVAL_MAX,
            )
            await asyncio.sleep(interval)
            elapsed += interval

            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    response = await client.get(
                        f"{PIAPI_BASE_URL}/task/{task_id}",
                        headers=self._headers(),
                    )
                    response.raise_for_status()
            except httpx.RequestError as exc:
                log.warning("piapi_poll_request_error", attempt=attempt, error=str(exc))
                continue

            data = response.json()
            task_data = data.get("data", {})
            status: str = task_data.get("status", "")

            status_lower = status.lower()
            log.info("piapi_poll", attempt=attempt, status=status_lower, elapsed_s=round(elapsed, 1))

            if status_lower == "completed":
                return task_data

            if status_lower == "failed":
                error_msg = task_data.get("error", {}).get("message", "Unknown error")
                raise APIError(f"PiAPI task failed: {error_msg}")

        raise TaskTimeoutError(
            f"Task did not complete after {max_attempts} attempts ({elapsed:.0f}s).",
            task_id=task_id,
            elapsed_seconds=elapsed,
        )

    async def create_and_poll(
        self,
        model: str,
        task_type: str,
        input_payload: dict,
    ) -> dict:
        """Create a task and poll until completion. Convenience method.

        Returns:
            Completed task data dict.
        """
        task_id = await self.create_task(model, task_type, input_payload)
        return await self.poll_task(task_id)
