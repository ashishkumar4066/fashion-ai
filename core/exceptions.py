"""
Custom exception hierarchy for the fashion-ai bot.

All domain errors inherit from FashionBotError. Handlers catch the
base class for generic logging and catch specific subclasses for
targeted user messaging.
"""


class FashionBotError(Exception):
    """Base exception for all fashion-ai domain errors.

    Attributes:
        message: Human-readable description of the error.
    """

    def __init__(self, message: str = "An unexpected error occurred.") -> None:
        self.message = message
        super().__init__(message)

    def __str__(self) -> str:
        return self.message


class ImageValidationError(FashionBotError):
    """Raised when an input image fails validation checks.

    Covers: file too large, unsupported mime type, dimensions out of range,
    or corrupted/unreadable image data.
    """


class APIError(FashionBotError):
    """Raised when an external API call fails.

    Covers: PiAPI HTTP errors, unexpected response shapes, or task
    status == 'Failed' from Kling AI.
    """


class RateLimitError(FashionBotError):
    """Raised when a user has exceeded their daily usage quota.

    Attributes:
        limit: The daily limit that was exceeded.
        reset_time: ISO 8601 UTC timestamp when the quota resets.
    """

    def __init__(
        self,
        message: str = "Daily limit reached.",
        limit: int = 0,
        reset_time: str = "",
    ) -> None:
        self.limit = limit
        self.reset_time = reset_time
        super().__init__(message)


class StorageError(FashionBotError):
    """Raised when a Cloudflare R2 (or compatible S3) operation fails.

    Covers: upload failures, download failures, permission errors.
    """


class TaskTimeoutError(FashionBotError):
    """Raised when a PiAPI task polling loop exceeds the maximum wait time.

    Attributes:
        task_id: The PiAPI task ID that timed out.
        elapsed_seconds: Total seconds waited before giving up.
    """

    def __init__(
        self,
        message: str = "Task timed out.",
        task_id: str = "",
        elapsed_seconds: float = 0.0,
    ) -> None:
        self.task_id = task_id
        self.elapsed_seconds = elapsed_seconds
        super().__init__(message)
