# CLAUDE.md — fashion-ai

AI Fashion Virtual Try-On Bot for Telegram. Users upload a person photo + garment photo and get back AI-generated model photos and videos via Kling AI (PiAPI).

**Stack:** Python 3.11, FastAPI, python-telegram-bot 21.x, Celery, Redis, Cloudflare R2, PiAPI/Kling, rembg, Pillow, pydantic-settings, structlog, httpx

**Setup:**
```bash
pip install -r requirements.txt
pip install python-magic-bin  # Windows only
```

---

## Components

### Component 1 — Image Processor ✅
Validates and normalizes input images before sending to the AI pipeline.
- Input: raw image bytes + filename
- Output: validated bytes, resized JPEG bytes, background-removed PNG bytes, base64 string
- Approach: `python-magic` for mime detection (never trust extension); Pillow for resize + RGB conversion; rembg U2Net for background removal (garment only); all async via `asyncio.to_thread()`
- File: `services/image_processor.py`

### Component 2 — PiAPI Client
Async wrapper for Kling AI task creation and polling via PiAPI.
- Input: model name, task type, input payload dict
- Output: completed task data dict (contains image_url or video_url)
- Approach: `httpx` async client; POST to create task, GET to poll; linear back-off (5s → 15s, max 60 attempts); raises `APIError` on failure, `TaskTimeoutError` on timeout
- File: `clients/piapi_client.py`

### Component 3 — Storage Client
Low-level Cloudflare R2 client (S3-compatible via boto3).
- Input: object key, bytes, content-type
- Output: upload confirmation, pre-signed URL
- Approach: `boto3` with `endpoint_url` pointing to R2; R2 key format `users/{user_id}/{type}/{uuid}/{filename}`; pre-signed URLs (3600s results, 86400s bundles); no trailing slash on endpoint URL
- File: `clients/storage_client.py`

### Component 4 — Asset Storage Service
High-level storage operations (upload inputs, results, ZIP bundles).
- Input: image/video bytes, user_id, task_id
- Output: public or pre-signed R2 URL
- Approach: wraps storage client; generates UUIDs for keys; creates ZIP bundles in-memory before uploading; sets ContentType explicitly on every upload
- File: `services/asset_storage.py`

### Component 5 — Try-On Service
Orchestrates the full virtual try-on pipeline end to end.
- Input: user_id, person image URL, garment image URL, garment type
- Output: result image URL (stored in R2)
- Approach: fetch inputs from R2 → preprocess → remove garment background → submit to PiAPI → poll → download result → upload to R2 → increment usage counter → return URL; result cached by sha256(inputs) for 24h
- File: `services/tryon_service.py`

### Component 6 — Pose Engine
Manages named pose variations for multi-pose generation.
- Input: pose key string
- Output: text prompt string for PiAPI video input
- Approach: static `POSE_LIBRARY` dict mapping pose keys to descriptive prompts (e.g. "standing facing forward, natural lighting"); generates Telegram InlineKeyboardMarkup for pose selection UI
- File: `services/pose_engine.py`

### Component 7 — Video Generator
Generates animated fashion videos from a try-on result image.
- Input: try-on result image URL, pose prompt, duration (5 or 10s)
- Output: MP4 URL (stored in R2)
- Approach: sends result image + prompt to PiAPI video generation; 9:16 aspect ratio for mobile/reels; polls until complete; uploads MP4 to R2; delivers via bot.send_video() or URL if > 50MB
- File: `services/video_generator.py`

### Component 8 — Usage Tracker / Rate Limiter
Enforces per-user daily limits and per-request cooldown.
- Input: user_id, action type (tryon / video)
- Output: bool (allowed or not), remaining quota
- Approach: Redis INCR with EXPIREAT end-of-day for daily counters; Redis SET NX with 5s TTL for per-request cooldown; limits configurable via env (`MAX_DAILY_TRYON_PER_USER`, `MAX_DAILY_VIDEO_PER_USER`)
- File: `bot/middleware/rate_limit.py`

### Component 9 — Celery Workers
Executes long-running tasks (try-on, video) outside the request cycle.
- Input: user_id, image URLs, chat_id (passed as task args)
- Output: result delivered to user via bot.send_photo() / send_video()
- Approach: Redis as broker (DB 0) and result backend (DB 1); tasks are synchronous wrappers using `asyncio.run()` at the boundary; max 3 retries on transient failure; separate queues for tryon and video tasks
- File: `workers/celery_app.py`, `workers/tasks/`

### Component 10 — Telegram Bot *(future phase)*
Handles conversation flow, image collection, and result delivery.
- Input: Telegram Update (photo, command, callback query)
- Output: messages, inline keyboards, images, videos sent to user
- Approach: python-telegram-bot 21.x async; ConversationHandler for multi-step photo collection; session state in Redis JSON (TTL 1800s); rate limit check before every handler; callback_data format `"{action}:{payload}"`
- File: `bot/`

### Component 11 — FastAPI Webhook *(future phase)*
Receives Telegram updates via webhook and routes to the bot.
- Input: POST /webhook/telegram (JSON update from Telegram)
- Output: HTTP 200 ACK (immediate); update forwarded to bot application
- Approach: FastAPI + uvicorn; validates X-Telegram-Bot-Api-Secret-Token header; stateless (all state in Redis); health endpoints at /health and /ready
- File: `api/`

---

## Phase Build Order

**Phase 1 — Backend MVP:**
1. `core/` — config, exceptions, constants, logging ✅
2. `core/redis_client.py`
3. `clients/piapi_client.py`
4. `clients/storage_client.py`
5. `services/image_processor.py` ✅
6. `services/asset_storage.py`
7. `models/` — session, task, user
8. `bot/middleware/rate_limit.py`
9. `services/tryon_service.py`
10. `workers/` — celery_app + tryon_tasks

**Phase 2 — Telegram Integration:**
11. `bot/` — handlers, keyboards, states
12. `api/` — FastAPI webhook

**Phase 3 — Advanced:**
Pose engine, video generator, batch try-on, observability, scaling
