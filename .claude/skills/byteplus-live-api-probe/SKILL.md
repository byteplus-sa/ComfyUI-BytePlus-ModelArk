---
name: byteplus-live-api-probe
description: How to check real BytePlus API behaviour (ModelArk, Seed Speech, AI MediaKit, asset library) safely before encoding it in this node pack — which values a parameter accepts, which media a model takes, response shapes, error codes, regional availability. Covers where probe scripts go, reading keys without leaking them, keeping cost low, cleaning up remote tasks, and recording the result. Use when docs are ambiguous or contradict responses, before tightening a parser or validation, when adding a model whose limits are unclear, or when the user says "check it live" / "does the API accept X".
---

# Live API probes

Tests never call the real API (CLAUDE.md → Commands). Probes are the separate, deliberate step that tells you what to write in the code and the tests. Examples already in the code: `SEEDANCE_DRAFT_FINAL_RESOLUTIONS` ("verified against the live API"), `SEED_LLM_MODEL_MAP` and the input-modality comment below it in `models_config.py` ("checked live").

## Before you probe

- **Ask the user first when it costs money beyond a few cents** (video generation, 4K, long audio, MediaKit enhancement). Say what you'll call, how many times, and the cheapest settings you'll use. Text/LLM, ASR on a 2 s clip and metadata calls (list models, get task, list assets) are fine to run directly.
- Check the docs first (`arkcli-docs` skill, Seed Speech docs mirror); probe only what they leave open.
- Prefer existing tools when they answer the question without code: `arkcli models` / `arkcli +chat` / `arkcli +gen` (arkcli-* skills) or the `ark-mcp` MCP tools. Write a script when you need the exact request this pack sends.

## Writing the probe

- Put scripts in the session scratchpad, never in the repo.
- Read keys from the same places the nodes do, and never print them:
  - ModelArk: `api_keys.json` (list of `{customName, apiKey, accessKey?, secretKey?}`) or ask the user which entry to use. AK/SK only for the asset library.
  - Seed Speech: `speech_api_keys.json` or `BYTEPLUS_SEED_SPEECH_API_KEY`.
  - MediaKit: `mediakit_api_keys.json` or `BYTEPLUS_VOD_MEDIAKIT_API_KEY`.
  - Print `key[:4] + "…"` at most. Don't echo request headers or full SDK errors that include them.
- Reuse the pack's own request builders where possible (`nodes_speech.build_*_request`, `nodes_mediakit.build_*_request`, `nodes_seed` payload code) so you test the request the node actually sends. Run with the interpreter from the `local-comfyui-test-env` memory (it has the Ark SDK, aiohttp, PyAV).
- Cheapest settings: smallest/fastest model in the family, 480p, minimum duration, draft mode, one image, `max_output_tokens` low, a 1–2 s audio clip, a tiny test video.
- Vary one thing at a time and keep a control call that is known to work, so a rejection is attributable to the value under test.
- Capture for each call: request (minus keys), HTTP status, error `code`/`message`, request ID, and the relevant part of the response.

## Clean up

- Cancel/delete any Seedance task you no longer need (`content_generation.tasks.delete`).
- Delete test files uploaded to the Ark Files API and test assets/groups you created, unless the user wants to keep them.
- Leave no scratch keys in files; don't touch the user's saved keys.

## Record the result

- Put the finding next to the code it justifies, in one comment: what was checked live and when (`# checked live 2026-10-01: …`). Update the CLAUDE.md row or Gotchas when it changes how the pack must be maintained (e.g. "the API accepts `max` on every listed model").
- Encode it in a unit test with fakes (expected request body / accepted options), so a later refactor can't silently undo it.
- When docs and responses disagree, keep the code tolerant of both and note it in CLAUDE.md Gotchas (see the Seed Speech entry) rather than following only one.
- Tell the user what you called, what it cost roughly, and what you concluded — including surprises and anything you could not confirm.
