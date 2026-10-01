---
name: byteplus-new-service-node
description: Recipe for adding or extending nodes on BytePlus products that do not use the ModelArk key — Seed Speech (Seed Audio, TTS, ASR, voice clone), VOD AI MediaKit (vCube, Smoothness, Image Enhance) or a new product with its own API key. Covers the key store and client node, the separate socket, the api_key_saved store, the transport with a test-replaceable _send, interrupts, uploads, async task polling, ExecutionBlocker outputs, tests, and the known Seed Speech API quirks. Use when adding a MediaKit tool, a Seed Speech endpoint, a new BytePlus product, or debugging one of these nodes.
---

# Nodes on non-ModelArk BytePlus products

Seed Speech (`nodes_speech.py` + `speech_api.py`) and AI MediaKit (`nodes_mediakit.py`) are the two worked examples. Copy whichever is closer: MediaKit for JSON task APIs (submit → poll → download), Speech for streamed/one-shot APIs. Generic V3 API questions: `comfyui-v3-nodes`.

## 1. A new product = a new key type

Keys are never interchangeable (CLAUDE.md → "Three key types"). A new product needs all of:

| Piece | MediaKit example | Notes |
|---|---|---|
| Base URLs + env var + console URL | `MEDIAKIT_REGION_BASE_URLS`, `MEDIAKIT_API_KEY_ENV`, `MEDIAKIT_API_KEYS_CONSOLE_URL` in `constants.py` | One region today; still keep the dict |
| Key file | `ApiKeyStore(<repo root>/mediakit_api_keys.json)` | Add the file to `.gitignore` **and** the CLAUDE.md runtime-files gotcha |
| Client object | `MediaKitClient(api_key, region)` with a `__repr__` that hides the key | Never put the key in outputs or logs |
| Socket | `comfy_io.Custom("BYTEPLUS_MEDIAKIT_CLIENT")` | Distinct type so a wrong client can't be wired |
| Client node | `BytePlusMediaKitClient`: `new_api_key`, `new_key_name`, `key_name` (saved keys + `Environment (<ENV>)` + `Custom`), `region`; `hidden=[Hidden.unique_id]` | Copy `execute` verbatim and rename |
| Save notification | `_notify_api_key_saved(node_id, name, key, store="mediakit")` | Add the store → class name to `API_CLIENT_CLASS_BY_STORE` in `web/js/byteplus_dynamic_widgets.js`, or the pasted key is never cleared |
| Registration | module `NODES` list → `__init__.py` `_registered_nodes` | Category `f"{GLOBAL_CATEGORY}/<Product>"` |
| Docs | README setup section for the key, CLAUDE.md layout row + "Key types" rule | |

Adding a tool/endpoint to an existing product needs none of this — skip to §2.

## 2. Transport

One module-level coroutine does the HTTP call and nothing else, so tests can swap it:

- MediaKit: `_send(client, method, path, body, timeout_seconds) -> (status, json)`; `mediakit_request` wraps it in `wait_interruptible`, maps `aiohttp`/timeout errors to `err_mediakit_network`, and API errors through `describe_mediakit_error` (adds the console hint on 401).
- Speech: `speech_api._send(method, url, headers, body, timeout)`; `speech_post` adds `X-Api-Key` and a fresh `X-Api-Request-Id`, and `describe_speech_error` maps numeric codes.

Rules: every await on the network goes through `nodes_shared.wait_interruptible`; sleeps use `nodes_shared.sleep_interruptible` (shared by Speech, MediaKit and the asset library — don't add per-module copies); `InterruptProcessingException` is always re-raised; all text comes from `constants.MESSAGES` with a `[BytePlus]` prefix (`_plain()` strips it for progress text).

## 3. Async tasks (MediaKit pattern)

`submit_and_wait(client, path, body, node_id, submitted_key)` → `POST` → poll `GET /tasks/{id}` every `MEDIAKIT_POLL_SECONDS` with `_send_progress_text` status under the node → `task_result`. Result URLs expire (24 h) — download inside `execute`. Synchronous tools (`/tools-sync/...`) pass a longer `timeout_seconds` instead of polling.

## 4. Media in and out

- Inputs: images/audio inline as base64 when the API takes it; otherwise upload through Comfy.org storage with the shared, cached `nodes_shared.upload_bytes_to_comfy_storage` (wrapped per product by `nodes_mediakit.upload_source` / `upload_image_source` and `nodes_speech.upload_to_comfy_storage`). It lazily imports `comfy_api_nodes.util` and needs a Comfy.org login. Build on it rather than writing a new upload path. Never register local media as a private asset.
- Validate limits **before** uploading (`validate_source_video`, `plan_image_enhance`) so the user doesn't pay for an upload that the API rejects.
- Offer both a socket and a `*_url` input where the API accepts links; `check_source` rejects both-at-once.
- Local video work uses PyAV (`_render_synced`, `nodes_shared.probe_video_file`); no OpenCV.
- An output that has nothing to show this run returns `ExecutionBlocker(None)` (downstream nodes are skipped instead of failing on `None`). If the API returns nothing because nothing needed doing, pass the source through (Smoothness).

## 5. Tests

- Speech: `tests/test_model_updates.py` / `tests/test_core_style_seed.py`, fake replacing `speech_api._send` (records requests, returns queued responses).
- MediaKit: `tests/test_mediakit.py`, `FakeMediaKit` replacing `nodes_mediakit._send` via `mock.patch.object`, scripted task statuses, `(http_status, body)` tuples for errors.
- Test request builders (`build_enhance_request`, `build_smoothness_request`, the Speech `build_*` functions) directly as pure functions; keep them module-level for that reason.
- Cover: request body, error mapping, interrupt during poll, the ExecutionBlocker/pass-through path, and limit validation. Never hit the real API — use `byteplus-live-api-probe` for that, separately.
- If the node mirrors a core node (vCube does), add a schema parity test (`test_matches_core_vcube_node`).

## 6. Seed Speech quirks (confirm live before tightening)

- Host `voice.ap-southeast-1.bytepluses.com` only; ModelArk keys fail with `45000010 Invalid X-Api-Key`.
- Success code is `0` for Seed Audio and `20000000` for TTS/ASR. Standard ASR status is in the `X-Api-Status-Code` **header** (`20000001/2` pending, `20000003` silent audio), queried with the same `X-Api-Request-Id` as the submit.
- TTS: model goes in `X-Api-Resource-Id`; `additions` is a JSON **string**; the body is a stream of JSON objects with base64 PCM (`iter_json_objects`). Subtitle chunks are undocumented.
- Seed Audio subtitles arrive as `sentences` or `utterances`.
- `seed_speech_voices.py` is generated from the official voice list page (URL and date in its header). Speaker IDs are saved in workflows: **append** new voices, never rename or remove; update the header date.
- Docs mirror and a working reference client: see the `local-comfyui-test-env` memory.
