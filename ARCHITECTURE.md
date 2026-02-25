# Architecture — AI Fashion Virtual Try-On Bot

## System Overview

A Telegram-native AI fashion assistant that enables small apparel brands to generate
professional model photos from a simple product image. The system uses Kling AI (via PiAPI)
for virtual try-on and video generation, delivered through an async Python backend.

```
┌─────────────┐      HTTPS       ┌─────────────────┐
│  User        │ ──────────────▶ │ Telegram Servers │
│ (Telegram)   │ ◀────────────── │                 │
└─────────────┘                  └────────┬────────┘
                                          │ POST /webhook/telegram
                                          ▼
                                 ┌─────────────────┐
                                 │  FastAPI App     │
                                 │  (uvicorn)       │
                                 │                  │
                                 │ ① Immediate ACK  │
                                 │ ② Route to bot   │
                                 └────────┬────────┘
                                          │
                          ┌───────────────▼──────────────┐
                          │   python-telegram-bot         │
                          │   Application (async)         │
                          │                               │
                          │  Handlers → session check     │
                          │  Rate limit → enqueue task    │
                          └────────────┬─────────────────┘
                                       │ .delay()
                                       ▼
                          ┌─────────────────────────┐
                          │   Celery Workers         │
                          │                          │
                          │  ┌──────────────────┐   │
                          │  │ TryOn Worker      │   │
                          │  │ ① Fetch from R2   │   │
                          │  │ ② ImageProcessor  │   │
                          │  │ ③ rembg (garment) │   │
                          │  │ ④ PiAPI/Kling     │   │
                          │  │ ⑤ Store to R2     │   │
                          │  │ ⑥ Send to user    │   │
                          │  └──────────────────┘   │
                          │                          │
                          │  ┌──────────────────┐   │
                          │  │ Video Worker      │   │
                          │  │ (same pattern)    │   │
                          │  └──────────────────┘   │
                          └─────────────────────────┘
                                    │         │
                          ┌─────────┘         └──────────┐
                          ▼                              ▼
               ┌─────────────────┐           ┌──────────────────┐
               │  PiAPI / Kling  │           │  Cloudflare R2   │
               │  (AI inference) │           │  (asset storage) │
               └─────────────────┘           └──────────────────┘

                          Redis
               ┌──────────────────────────────┐
               │  DB 0: Sessions, rate limits  │
               │  DB 0: Celery broker          │
               │  DB 1: Celery results         │
               └──────────────────────────────┘
```

---

## Component Architecture

### 1. FastAPI + Webhook Layer (`api/`)

**Responsibility:** Receive Telegram updates via webhook, ACK immediately, forward to bot.

- Stateless — all state lives in Redis
- Validates `X-Telegram-Bot-Api-Secret-Token` header on every request
- Returns HTTP 200 within milliseconds (Telegram drops requests > 5s)
- Health endpoints (`/health`, `/ready`) for load balancer probes
- Factory pattern: `create_app()` returns a configured FastAPI instance

**Key files:**
- `api/main.py` — app factory, lifespan hooks (bot init/shutdown)
- `api/routers/webhook.py` — POST /webhook/telegram endpoint
- `api/routers/health.py` — health probes
- `api/dependencies.py` — FastAPI Depends injection (Redis, storage)

---

### 2. Telegram Bot (`bot/`)

**Responsibility:** Conversation flow, user UX, inline keyboards, result delivery.

**Conversation state machine:**
```
IDLE
  │ /start or image received
  ▼
AWAITING_PERSON      ← user sends person photo
  │
  ▼
AWAITING_GARMENT     ← user sends garment photo
  │
  ▼
SELECTING_OPTIONS    ← user sees action keyboard (Try On / Change Model)
  │
  ▼
PROCESSING           ← task dispatched to Celery
  │
  ▼
RESULT_READY         ← user sees result + secondary options (Pose / Video / Download)
  │
  ▼
IDLE                 ← user resets or session expires (30 min TTL)
```

**Session state** stored in Redis as JSON (not in-memory) — safe for multi-replica deployments.

**Key files:**
- `bot/states.py` — state string constants
- `bot/handlers/start.py` — /start, /help, /status
- `bot/handlers/image_upload.py` — photo message handler (primary UX entry point)
- `bot/handlers/callbacks.py` — InlineKeyboard callback router
- `bot/handlers/download.py` — result delivery (photo/video/ZIP)
- `bot/middleware/rate_limit.py` — per-user cooldown + daily limit check

---

### 3. Image Processor (`services/image_processor.py`)

**Responsibility:** Validate and normalize input images before API submission.

**Pipeline:**
```
raw bytes
    │
    ├─ validate(): size ≤ 20MB, mime ∈ allowed set, dimensions ≥ 256px
    │
    ├─ preprocess(): resize to max 2048px, convert to RGB, JPEG quality 95
    │
    └─ remove_background(): rembg U2Net (garment images only) → PNG with alpha
```

**Design decisions:**
- Use `python-magic` for mime detection (not file extension)
- `rembg` runs synchronously in the Celery worker (CPU-bound, not in asyncio loop)
- Background removal only on garment — never on person images
- rembg session is lazy-loaded on first call via `_get_rembg_session()` to avoid cold-start delay

---

### 4. PiAPI Client (`clients/piapi_client.py`)

**Responsibility:** Reliable async wrapper for PiAPI's Kling AI task API.

**Endpoints used:**
```
POST https://api.piapi.ai/api/v1/task
  Body: {model, task_type, input: {...}}
  Response: {code, data: {task_id, status}}

GET https://api.piapi.ai/api/v1/task/{task_id}
  Response: {code, data: {task_id, status, output: {image_url | video_url}}}
```

**Task status flow:** `Pending → Staged → Processing → Completed | Failed`

**Polling strategy:**
- Start at 5s interval
- Increase by 2s per attempt, cap at 15s
- Maximum 60 attempts (~5 minutes total)
- Log every attempt with attempt number, task_id, current status
- Raise `TaskTimeoutError` after max attempts
- Raise `APIError` on `Failed` status

**Request config:** `timeout=120` on all httpx requests; retry transport errors 3x with exponential backoff.

---

### 5. Try-On Service (`services/tryon_service.py`)

**Responsibility:** Orchestrate the complete virtual try-on pipeline.

**Pipeline (10 steps):**
1. Fetch person + garment images from R2
2. Validate and preprocess both images (ImageProcessor)
3. Remove background from garment image (rembg)
4. Upload preprocessed images to R2 temp prefix
5. Submit to PiAPI: `model=kling`, `task_type=virtual_try_on`
   - Input: `{human_image: url, cloth_image: url, cloth_type: "upper"}`
6. Poll until `Completed`
7. Download result image from PiAPI output URL
8. Upload result to R2: `users/{id}/result/{task_id}/result.jpg`
9. Increment `usage:tryon:{user_id}:{date}` in Redis
10. Return permanent result URL

**Result caching:** Cache key `result_cache:{sha256(person_url+garment_url+garment_type)}` — if same inputs within 24h, return cached result without calling PiAPI.

---

### 6. Pose Engine (`services/pose_engine.py`)

**Responsibility:** Manage named pose variations for multi-pose generation.

**Pose library (static, extensible):**
```python
POSE_LIBRARY = {
    "front_casual":    "standing facing forward, neutral relaxed pose, natural lighting",
    "side_profile":    "standing 3/4 side view, elegant pose, studio lighting",
    "walking_dynamic": "walking pose, dynamic movement, street style lighting",
    "sitting_fashion": "sitting pose, fashion editorial style, high key lighting",
    "outdoor_lifestyle":"outdoor setting, natural light, lifestyle fashion pose",
}
```

Generates `InlineKeyboardMarkup` for Telegram pose selection UI.
Maps `pose_key → prompt string` for injection into PiAPI video generation input.

---

### 7. Video Generator (`services/video_generator.py`)

**Responsibility:** Generate animated fashion videos from try-on result images.

**Pipeline:**
1. Receive best try-on result URL + selected pose prompt
2. Submit to PiAPI: `model=kling`, `task_type=video_generation`
   - Input: `{image_url: result_url, prompt: pose_prompt + " fashion model smooth motion", duration: 5, aspect_ratio: "9:16"}`
3. Poll until `Completed`
4. Download MP4 from PiAPI output URL
5. Upload to R2: `users/{id}/result/{task_id}/video.mp4`
6. Return MP4 URL

**Delivery:** Use `bot.send_video()` for files ≤ 50 MB; send URL directly for larger files.

---

### 8. Asset Storage Service (`services/asset_storage.py`)

**Responsibility:** All Cloudflare R2 operations.

**R2 key format:** `users/{user_id}/{type}/{uuid4}/{filename}`
- `type`: `input` | `result` | `bundle`

**Operations:**
- `upload_image(bytes, user_id, filename) → url`
- `upload_result(bytes, user_id, task_id) → url`
- `create_zip_bundle(urls, user_id) → url` — downloads all URLs, ZIPs in memory, uploads once
- `get_presigned_url(key, expires) → url` — 3600s for results, 86400s for bundles

**R2 vs S3:** Zero egress fees — critical because workers frequently download from storage.
The `storage_client.py` wrapper abstracts the boto3 details; switching to S3 requires only config change.

---

### 9. Usage Tracker + Rate Limiter (`bot/middleware/rate_limit.py`)

**Responsibility:** Enforce per-user daily limits and per-request cooldown periods.

**Redis keys:**
```
usage:tryon:{user_id}:{YYYY-MM-DD}   INCR + EXPIREAT end-of-day UTC
usage:video:{user_id}:{YYYY-MM-DD}   INCR + EXPIREAT end-of-day UTC
ratelimit:{user_id}                  SET NX, TTL 5s (per-request cooldown)
```

**Limits (configurable via env):**
- `MAX_DAILY_TRYON_PER_USER` (default: 5)
- `MAX_DAILY_VIDEO_PER_USER` (default: 2)
- Per-request cooldown: 5 seconds

Returns remaining quota for display in `/status` command.

---

### 10. Celery Workers (`workers/`)

**Responsibility:** Execute long-running tasks outside the HTTP request cycle.

**Configuration:**
- Broker: Redis DB 0 (`CELERY_BROKER_URL`)
- Result backend: Redis DB 1 (`CELERY_RESULT_BACKEND`)
- Worker concurrency: 2 per container (I/O-bound, waiting on PiAPI)
- Task routing: `tryon_tasks → "tryon" queue`, `video_tasks → "video" queue`

**Key tasks:**
- `async_tryon(user_id, person_url, garment_url, chat_id)` — full try-on pipeline
- `async_generate_video(user_id, result_url, pose_key, chat_id)` — video pipeline
- `async_batch_tryon(user_id, person_url, garment_url, pose_keys, chat_id)` — fan-out with Celery chord
- `cleanup_expired_assets()` — nightly beat task, purge old R2 objects

**Sync/async boundary:** Celery tasks are synchronous; use `asyncio.run()` at the task entry point.

---

## Data Flow: Core Try-On Request

```
User sends garment photo
         │
         ▼
Telegram delivers POST /webhook/telegram
         │
         ▼
FastAPI → python-telegram-bot Application
  ① ACK 200 immediately
         │
         ▼
photo_handler fires:
  ② Check rate limit (Redis SET NX)
  ③ Download from Telegram CDN
  ④ Validate: mime type, size, dimensions
  ⑤ Upload to R2: users/{id}/input/{uuid}/garment.jpg
  ⑥ Update UserSession.garment_image_url (Redis)
  ⑦ Send confirmation + action keyboard to user
         │
User taps "Try On Now"
         │
         ▼
callbacks.py → async_tryon.delay(user_id, person_url, garment_url, chat_id)
         │
         ▼
Celery worker picks up task:
  ⑧  Fetch person + garment from R2
  ⑨  Preprocess images (Pillow: resize, RGB convert)
  ⑩  Remove background from garment (rembg)
  ⑪  Upload preprocessed to R2 temp prefix
  ⑫  POST to PiAPI /task (virtual_try_on)
  ⑬  Poll GET /task/{id} every 5–15s
  ⑭  On Completed: download result image
  ⑮  Upload result to R2: users/{id}/result/{task_id}/result.jpg
  ⑯  Increment usage counter (Redis INCR)
  ⑰  bot.send_photo(chat_id, result_url, caption="Your try-on result! 👕")
         │
         ▼
User receives result image with secondary action keyboard:
  [More Poses] [Create Video] [Download ZIP] [Start Over]
```

---

## API Integrations

### PiAPI / Kling AI

| | Detail |
|---|---|
| Base URL | `https://api.piapi.ai/api/v1` |
| Auth | `x-api-key: {PIAPI_API_KEY}` header |
| Create task | `POST /task` |
| Poll task | `GET /task/{task_id}` |
| Virtual try-on input | `{human_image, cloth_image, cloth_type: "upper"\|"lower"\|"overall"}` |
| Video generation input | `{image_url, prompt, duration: 5\|10, aspect_ratio: "9:16"}` |
| Task states | `Pending → Staged → Processing → Completed \| Failed` |
| Max latency | ~3–5 minutes for video generation |

Check `x-ratelimit-remaining` response header to proactively back off before hitting account limits.

### Cloudflare R2

| | Detail |
|---|---|
| Client | `boto3` with `endpoint_url` pointing to R2 account endpoint |
| Key operations | `put_object`, `get_object`, `generate_presigned_url`, `delete_object` |
| Auth | R2 API token (Access Key ID + Secret Access Key) |
| Egress fees | None — key cost advantage over AWS S3 |
| Bucket access | Private — access via pre-signed URLs only |

### Telegram Bot API

| | Detail |
|---|---|
| Library | `python-telegram-bot` 21.x |
| Mode (prod) | Webhook — POST updates to `/webhook/telegram` |
| Mode (local dev) | Polling — `Application.run_polling()` |
| Photo send limit | 10 MB |
| Document/video send limit | 50 MB |
| File download | `bot.get_file(file_id)` then download to bytes; links expire — download immediately |

---

## Phased Build Plan

### Phase 1 — MVP (End-to-End Try-On)

**Goal:** User uploads two photos, bot returns try-on image. Single flow, basic rate limiting.

| Step | Component | Key decisions |
|---|---|---|
| 1 | `core/` foundation | pydantic-settings, structlog, custom exceptions ✅ |
| 2 | Redis client | asyncio pool, shared singleton |
| 3 | PiAPI client | httpx, polling with back-off, retry on transport errors |
| 4 | R2 storage client | boto3 + endpoint_url, pre-signed URLs |
| 5 | Image processor | Pillow + rembg + python-magic ✅ |
| 7 | Asset storage service | wraps storage client, key naming scheme |
| 8 | Data models | UserSession, TryOnTask, UserProfile dataclasses |
| 9 | Rate limiter | Redis NX lock + daily INCR counter |
| 10 | Try-on service | orchestrates steps 4–7 |
| 11 | Celery tasks | sync wrapper, asyncio.run boundary, user notification |
| 12 | Bot handlers | start, image_upload, callbacks, download |
| 13 | FastAPI webhook | webhook endpoint, health probes |

**MVP definition of done:** User sends person photo, sends garment photo, taps "Try On", receives result image in < 3 minutes.

### Phase 2 — Advanced Features

- **Pose engine** — named pose library, multi-pose batch (Celery chord fan-out), ZIP bundle delivery
- **Video generator** — Kling video pipeline, 5–10s MP4 output, 9:16 aspect ratio
- **Admin handlers** — `/stats`, `/broadcast` (gated by `ADMIN_TELEGRAM_IDS`)
- **Webhook security** — validate `X-Telegram-Bot-Api-Secret-Token` on every request
- **PiAPI webhooks** — replace polling with callback URL delivery (reduces idle wait load)

### Phase 3 — Production Hardening

- **Observability** — Prometheus metrics (FastAPI + Celery), structured JSON logs, health dashboards
- **PostgreSQL** — user profiles, task history beyond Redis TTL, usage analytics (optional upgrade)
- **Horizontal scaling** — stateless bot API (multi-replica), Celery worker scale-out
- **Cost management** — result cache (sha256 of inputs), 7-day R2 object expiry cleanup worker
- **Security hardening** — per-user R2 storage quota, IP rate limiting at nginx, secrets via cloud secret manager

---

## Configuration and Secrets

All configuration via `pydantic-settings` (`core/config.py`) reading environment variables.

- **Development:** `.env` file in the project root (loaded automatically by pydantic-settings)
- **Production:** env vars injected by the hosting environment (never commit `.env` to git)
- `.env.example` is the canonical reference — keep it up to date with every new variable added

---

## Scalability Model

| Component | Scaling approach |
|---|---|
| FastAPI bot API | Stateless — add replicas behind nginx upstream |
| Celery workers | Run multiple worker processes; scale as needed |
| Redis | Single instance for dev; Redis Cluster or managed Redis (Upstash) for prod |
| Cloudflare R2 | Inherently scalable — no action needed |
| PiAPI | Subject to account rate limits; use Celery queue depth as backpressure signal |

---

## Security Model

| Concern | Mitigation |
|---|---|
| Telegram webhook spoofing | Validate `X-Telegram-Bot-Api-Secret-Token` on every request |
| Unauthorized access to assets | R2 private bucket; user-specific pre-signed URLs with short TTL |
| PII storage | Only `user_id` (integer) stored — no names, phone numbers, or profile data |
| Abuse / cost runaway | Per-user daily limits + 5s cooldown enforced in Redis |
| Admin command abuse | Admin commands gated by hardcoded `ADMIN_TELEGRAM_IDS` list |
| Malicious file uploads | Validate mime type with `python-magic`; never trust file extension |
| Secrets exposure | No secrets in R2 metadata, logs, or error messages |

---

## Error Handling Strategy

| Error type | Handling |
|---|---|
| Image validation failure | Immediate user-friendly message, prompt to retry with a different image |
| PiAPI task `Failed` | Log with task details; user informed to retry; Celery retries 3x on transient failures |
| PiAPI timeout (> 5 min) | `TaskTimeoutError` raised; user notified; task marked failed |
| R2 unavailable | `StorageError` raised; user sees "service temporarily unavailable"; log critical |
| Redis unavailable | Log critical; bot handler returns generic error; no retry (Redis is foundational) |
| Rate limit exceeded | `RateLimitError`; user sees quota remaining and reset time; no Celery task dispatched |
| Unknown/unexpected | Log with full context; user sees "Something went wrong, please try again" |

**Principle:** Never expose stack traces, internal URLs, or API keys to users. All user-facing messages are friendly, actionable, and in plain language.
