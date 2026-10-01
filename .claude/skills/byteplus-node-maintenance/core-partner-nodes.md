# ComfyUI core's ByteDance partner nodes — comparison notes

Core ComfyUI ships its own ByteDance nodes in `comfy_api_nodes/nodes_bytedance.py` (+ `apis/bytedance.py` pydantic models). They call BytePlus **through the Comfy.org proxy** and bill Comfy credits; this pack calls ModelArk **directly** with the user's key. Core usually adds new Seedream/Seedance models quickly, so its code is a useful early signal for payload fields, limits and error codes. Read on 2026-10-01 from Comfy-Org/ComfyUI `master` (651ca29; last ByteDance change 9f932548f, 2026-09-25). Core's retired models are deleted outright; core's partner-proxy Idempotency-Key (a8686f2b3) has no ModelArk equivalent, which is why paid calls here never auto-retry (`nodes_shared.call_billed`).

Read it with:

```bash
gh api repos/Comfy-Org/ComfyUI/contents/comfy_api_nodes/nodes_bytedance.py --jq .content | base64 -d > /tmp/nodes_bytedance.py
gh api "repos/Comfy-Org/ComfyUI/commits?path=comfy_api_nodes/nodes_bytedance.py&per_page=15" --jq '.[] | "\(.commit.author.date[:10]) \(.sha[:9]) \(.commit.message|split("\n")[0])"'
```

## Endpoints

Core: `/proxy/byteplus/api/v3/images/generations`, `/proxy/byteplus/api/v3/contents/generations/tasks` (+ `/{id}`); Seedance 2.x status via `/proxy/byteplus-seedance2/…`. Strip `/proxy/byteplus` → native ModelArk path under `https://ark.<region>.bytepluses.com/api/v3`.

## Model IDs (core, 2026-09-27) vs this pack

| Core | This pack (`models_config.py`) |
|---|---|
| `seedream-5-0-pro-260628` | `dola-seedream-5-0-pro-260628` |
| `seedream-5-0-flash-260915` (added 2026-09-24) | — not yet |
| `seedream-5-0-260128` (5.0 lite) | same |
| `seedream-4-5-251128`, `seedream-4-0-250828` | same |
| `dreamina-seedance-2-5-260628` (+ "Draft" variant = same ID with `draft`) | same |
| — | `dreamina-seedance-2-5-premium-260915` |
| `dreamina-seedance-2-0-260128`, `-fast-260128` | same |
| `dreamina-seedance-2-0-mini` (undated) | `dreamina-seedance-2-0-mini-260615` |
| `seedance-1-5-pro-251215`, `seedance-1-0-pro-250528`, `seedance-1-0-pro-fast-251015` | same |

Core's IDs are Comfy.org proxy aliases. The native BytePlus IDs carry BytePlus prefixes — e.g. Seedream 5.0 Pro is `dola-seedream-5-0-pro-260628` on BytePlus ("Dola" = BytePlus international prefix; Volcengine uses `doubao-`). **Never copy an ID from core; confirm it with `arkcli models list --modality image|video` (needs `arkcli auth login`) or the BytePlus model list.**

## Payload shapes (core's pydantic models)

- **Seedream** (`Seedream4TaskCreationRequest`): `model, prompt, response_format="url", image: list[str], size="WxH", seed (0..2147483647), sequential_image_generation, sequential_image_generation_options{max_images}, watermark, output_format, optimize_prompt_options{thinking|mode}`. 5.0 Pro/Flash: `sequential_*` = None.
  Limits core enforces: Pro/Flash 0.92–4.62 MP; 4.5 & 5.0 lite ≥ 3.68 MP; 4.0 ≥ 0.92 MP; all ≤ 16.78 MP; ≤ 14 refs (5.0 lite) / 10 (others); refs + outputs ≤ 15; ref aspect 1:16–16:1; `thinking` can only be disabled for text-to-image.
- **Seedance 1.x**: core appends `--resolution --ratio --duration --seed --camerafixed --watermark` to the prompt text (this pack sends JSON fields instead and forbids the flags in the prompt). `generate_audio` only for 1.5 pro (≥ 4 s). Frames: 300–6000 px, aspect 0.4–2.5. Content roles: `first_frame`, `last_frame`, `reference_image`.
- **Seedance 2.x** (`Seedance2TaskCreationRequest`): `model, content[], generate_audio, resolution, ratio, duration, seed, watermark, output_format, omni_reference_task_type (auto/reference/edit/extend), draft`. Content types: `text`, `image_url`, `video_url` (role `reference_video`), `audio_url` (role `reference_audio`), `draft_task{id}`. Edit → forces `ratio="adaptive", duration=-1`; extend → `ratio="adaptive"`. Reference limits 9 img / 3 vid / 3 audio / 15.1 s total (2.5: 30/10/10/30.1 s); each clip ≥ 1.8 s; min 409,600 px per reference video.
- Status: `queued | running | cancelled | succeeded | failed`; result `content.video_url`, `usage.completion_tokens`.

## Patterns worth copying

- Per-provider error-code translation into friendly messages (`_seedance2_poll_video_task`): `OutputAudioSensitiveContentDetected.PolicyViolation`, `InvalidParameter.TaskTypeConstraint`, `InvalidParameter.TaskTypeMismatch`.
- HTTP retry policy (`comfy_api_nodes/util/client.py`): retry `408/500/502/503/504` with backoff (3 retries, ×2), 429 handled separately honouring `Retry-After` (cap 150 s). This pack has **no** API retries today.
- Poll bounds: `max_poll_attempts=480` (queued polls not counted), per-poll timeout 120 s. This pack has no client-side poll limit (relies on task expiry + interrupt).
- Interruptible sleeps in 1 s slices checking `processing_interrupted()`.
- Status text under the node via `PromptServer.instance.send_progress_text(...)` ("Status: … / Time elapsed: …").
- Deprecation instead of deletion: old node IDs kept as `is_deprecated=True` subclasses so saved workflows still load.
- Draft → final: separate output for `draft_task_id`, and a guard that a non-draft model can't run while that output is linked.

## Patterns not to copy

- `is_api_node=True` / `price_badge` in Comfy credits — this pack is billed by BytePlus directly; the flag makes the frontend treat nodes as Comfy partner nodes and auto-adds Comfy auth inputs.
- Importing `sync_op` / `poll_op` from `comfy_api_nodes.util` — they assume Comfy auth, write request bodies to `<temp>/api_logs/`, and their signatures changed repeatedly in 2025–2026. The only acceptable import is the lazy, wrapped `upload_video_to_comfyapi`.
- Uploading references to Comfy.org and registering them as `asset://` via `/proxy/seedance/virtual-library/assets` — proxy-only; with a direct key use public URLs, Ark Files API, or user `asset://` IDs.

## Useful core helpers to mirror (not import)

`validate_image_dimensions`, `validate_image_aspect_ratio`, `validate_video_duration`, `validate_string`, `download_url_to_video_output` (streaming, retry, interruptible), `tensor_to_base64_string(t, total_pixels=2048*2048)`.
