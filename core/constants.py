"""
Shared constants for image processing and API interaction.

These values are not in config.py because they are not environment-dependent —
they represent fixed technical constraints of the pipeline, not operational tunables.
"""

# --- Image validation limits ---

MAX_IMAGE_BYTES: int = 20 * 1024 * 1024  # 20 MB

MIN_IMAGE_DIMENSION: int = 256  # px — reject images smaller than this on any axis

MAX_IMAGE_DIMENSION: int = 2048  # px — resize longest edge to this before API submission

# --- Image encoding ---

JPEG_QUALITY: int = 95  # High quality; reduces file size vs raw PNG without visible loss

# --- Mime type allowlist ---

SUPPORTED_MIME_TYPES: frozenset[str] = frozenset(
    {
        "image/jpeg",
        "image/png",
        "image/webp",
    }
)
