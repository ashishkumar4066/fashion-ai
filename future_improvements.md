# Future Improvements

Components and features deferred from the current build phase.

---

## Component 4 — Storage Client
Low-level Cloudflare R2 client (S3-compatible via boto3).
- Input: object key, bytes, content-type
- Output: upload confirmation, pre-signed URL
- Approach: `boto3` with `endpoint_url` pointing to R2; R2 key format `users/{user_id}/{type}/{uuid}/{filename}`; pre-signed URLs (3600s results, 86400s bundles); no trailing slash on endpoint URL
- File: `clients/storage_client.py`

## Component 5 — Asset Storage Service
High-level storage operations (upload inputs, results, ZIP bundles).
- Input: image/video bytes, user_id, task_id
- Output: public or pre-signed R2 URL
- Approach: wraps storage client; generates UUIDs for keys; creates ZIP bundles in-memory before uploading; sets ContentType explicitly on every upload
- File: `services/asset_storage.py`

---

## Asset Storage Architecture

### Asset Storage Service (`services/asset_storage.py`)

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

## Cloudflare R2 Integration

| | Detail |
|---|---|
| Client | `boto3` with `endpoint_url` pointing to R2 account endpoint |
| Key operations | `put_object`, `get_object`, `generate_presigned_url`, `delete_object` |
| Auth | R2 API token (Access Key ID + Secret Access Key) |
| Egress fees | None — key cost advantage over AWS S3 |
| Bucket access | Private — access via pre-signed URLs only |
