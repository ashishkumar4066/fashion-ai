"""
Structlog configuration for the fashion-ai application.

Call configure_logging() once at application startup before any other
code that logs.

Usage:
    from core.logging import configure_logging
    configure_logging()

    import structlog
    logger = structlog.get_logger(__name__)
    logger.info("event_name", user_id=123, key="value")
"""

import logging
import sys

import structlog

from core.config import settings


def configure_logging() -> None:
    """Configure structlog with JSON output (production) or colored console (development).

    - LOG_LEVEL=DEBUG  → colored console (local dev)
    - Any other level  → JSON output for log aggregators

    This function is idempotent — safe to call multiple times.
    """
    log_level_str = settings.LOG_LEVEL.upper()
    log_level = getattr(logging, log_level_str, logging.INFO)

    is_development = log_level_str == "DEBUG"

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
    ]

    if is_development:
        renderer: structlog.types.Processor = structlog.dev.ConsoleRenderer(colors=True)
        processors = shared_processors + [renderer]
    else:
        processors = shared_processors + [
            structlog.processors.dict_tracebacks,
            structlog.processors.JSONRenderer(),
        ]

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )

    # Configure stdlib logging so third-party libs (httpx, boto3) emit at the right level.
    logging.basicConfig(
        level=log_level,
        stream=sys.stdout,
        format="%(message)s",
    )
