"""
Application configuration via pydantic-settings.

All values are read from environment variables (or a .env file in development).
Import the singleton `settings` object rather than instantiating Settings directly:

    from core.config import settings
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central configuration for the fashion-ai application.

    Fields with empty string defaults are optional during the image processor
    development phase and become required in later phases (PiAPI, storage, bot).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",  # Silently ignore unknown env vars
    )

    # --- Logging ---
    LOG_LEVEL: str = "INFO"

    # --- PiAPI / Kling AI ---
    PIAPI_API_KEY: str = ""

    # --- Telegram ---
    TELEGRAM_BOT_TOKEN: str = ""
    WEBHOOK_URL: str = ""
    WEBHOOK_SECRET: str = ""

    # --- Redis ---
    REDIS_URL: str = "redis://localhost:6379/0"

    # --- Cloudflare R2 ---
    R2_BUCKET_NAME: str = ""
    R2_ACCESS_KEY_ID: str = ""
    R2_SECRET_ACCESS_KEY: str = ""
    R2_ENDPOINT_URL: str = ""
    R2_PUBLIC_BASE_URL: str = ""

    # --- Rate limits ---
    MAX_DAILY_TRYON_PER_USER: int = 5
    MAX_DAILY_VIDEO_PER_USER: int = 2

    # --- Admin ---
    ADMIN_TELEGRAM_IDS: str = ""  # Comma-separated integers, e.g. "123456,789012"


# Module-level singleton — import this, do not instantiate Settings() elsewhere.
settings = Settings()
