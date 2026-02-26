# CLAUDE.md — fashion-ai

AI Fashion Virtual Try-On Bot for Telegram. Users upload a person photo + garment photo and get back AI-generated model photos and videos via Kling AI (PiAPI).

**Stack:** Python 3.11, FastAPI, python-telegram-bot 21.x, Celery, Redis, Cloudflare R2, PiAPI/Kling/Gemini, rembg, Pillow, pydantic-settings, structlog, httpx

**Setup:**
```bash
pip install -r requirements.txt
pip install python-magic-bin  # Windows only

uvicorn api.main:app --reload --host 0.0.0.0 --port 8000
```

---

## Components

### Component 1 — Image Processor ✅
Validates and normalizes input images before sending to the AI pipeline.
- Input: raw image bytes + filename
- Output: validated bytes, resized JPEG bytes, background-removed PNG bytes, base64 string
- Approach: `python-magic` for mime detection (never trust extension); Pillow for resize + RGB conversion; rembg U2Net for background removal (garment only); all async via `asyncio.to_thread()`
- File: `services/image_processor.py`

### Component 2 — Model Generator ✅
Generates a photorealistic human model image from a text prompt. Output used as person_image in the try-on pipeline.
- Input: prompt string (e.g. "young Indian male, casual pose"), aspect_ratio (default "2:3")
- Output: local file path `data/model/{uuid}.jpg` + PiAPI image URL
- Approach: Gemini 2.5 Flash via PiAPI (`model="gemini"`, `task_type="gemini-2.5-flash-image"`); auto-prepends fashion-context prefix to prompt; downloads result and saves to `data/model/`
- File: `services/model_generator.py`

### Component 3 — PiAPI Client ✅
Async wrapper for Kling AI and Gemini task creation and polling via PiAPI.
- Input: model name, task type, input payload dict
- Output: completed task data dict (contains image_url or video_url)
- Approach: `httpx` async client; POST to create task, GET to poll; linear back-off (5s → 15s, max 60 attempts); raises `APIError` on failure, `TaskTimeoutError` on timeout
- File: `clients/piapi_client.py`

### Component 4 — Try-On Service
Orchestrates the full virtual try-on pipeline end to end.
- Input: user_id, person image URL, garment image URL, garment type
- Output: result image URL (stored in R2)
- Approach: fetch inputs from R2 → preprocess → remove garment background → submit to PiAPI Kling → poll → download result → upload to R2 → increment usage counter → return URL
- File: `services/tryon_service.py`

### Component 5 — Pose Engine
Manages named pose variations for multi-pose generation.
- Input: pose key string
- Output: text prompt string for PiAPI video input
- Approach: static `POSE_LIBRARY` dict mapping pose keys to descriptive prompts; generates Telegram InlineKeyboardMarkup for pose selection UI
- File: `services/pose_engine.py`

### Component 6 — Video Generator
Generates animated fashion videos from a try-on result image.
- Input: try-on result image URL, pose prompt, duration (5 or 10s)
- Output: MP4 URL (stored in R2)
- Approach: sends result image + prompt to PiAPI Kling video generation; 9:16 aspect ratio for mobile/reels; polls until complete; uploads MP4 to R2
- File: `services/video_generator.py`

### Component 7 — Usage Tracker / Rate Limiter
Enforces per-user daily limits and per-request cooldown.
- Input: user_id, action type (tryon / video)
- Output: bool (allowed or not), remaining quota
- Approach: Redis INCR with EXPIREAT end-of-day for daily counters; Redis SET NX with 5s TTL for per-request cooldown
- File: `bot/middleware/rate_limit.py`

### Component 8 — Celery Workers
Executes long-running tasks (try-on, video) outside the request cycle.
- Input: user_id, image URLs, chat_id (passed as task args)
- Output: result delivered to user via bot.send_photo() / send_video()
- Approach: Redis as broker (DB 0) and result backend (DB 1); tasks are synchronous wrappers using `asyncio.run()` at the boundary; max 3 retries on transient failure
- File: `workers/celery_app.py`, `workers/tasks/`

### Component 9 — Telegram Bot *(future phase)*
Handles conversation flow, image collection, and result delivery.
- Input: Telegram Update (photo, command, callback query)
- Output: messages, inline keyboards, images, videos sent to user
- Approach: python-telegram-bot 21.x async; ConversationHandler for multi-step photo collection; session state in Redis JSON (TTL 1800s); rate limit check before every handler
- File: `bot/`

---

## Phase Build Order

**Phase 1 — Backend:**
1. `core/` — config, exceptions, constants, logging ✅
2. `clients/piapi_client.py` ✅
3. `services/image_processor.py` ✅
4. `services/model_generator.py` ✅
5. `api/main.py` + `api/routers/model.py` ✅
6. `services/tryon_service.py`
7. `workers/` — celery_app + tryon_tasks

**Phase 2 — Telegram Integration:**
8. `bot/` — handlers, keyboards, states
9. FastAPI webhook endpoint

**Phase 3 — Advanced:**
Pose engine, video generator, batch try-on, observability, scaling
