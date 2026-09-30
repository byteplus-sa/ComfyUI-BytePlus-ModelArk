# ComfyUI BytePlus ModelArk

ComfyUI custom nodes for **BytePlus ModelArk**: Seedance video generation, Seedream image generation, and Seed multimodal understanding. Plus **BytePlus Seed Speech**: Seed Audio 1.0 generation, text to speech (TTS) and speech recognition (ASR).

Generation calls go directly to ModelArk with **your own ModelArk API key**, so usage is billed to your BytePlus account (including contract pricing and resource packs). No Comfy credits are used. Seed Speech nodes use a separate Seed Speech API key (see [Seed Speech API Key](#seed-speech-api-key)). The one exception is local reference videos for Seedance 2 / 2.5, which pass through Comfy.org storage (see [Reference Videos](#reference-videos)).

> **Status: in development (v0.3.0).** Nodes target BytePlus ModelArk regions and model IDs. See [Roadmap](#roadmap).

**v0.3.0:** Adds Seed Speech nodes: `Seed Audio 1.0`, `Seed Speech TTS`, `Seed Speech ASR`, `Seed Voice Clone` and the `Speech Client` that holds the Seed Speech API key.

**v0.2.2:** Seedance 2.5 Premium accepts 4K normal renders only; draft mode submits at 480p. Unsupported normal resolutions are rejected before submission.

## Features

- **Region selection**: `ap-southeast-1` (default) or `eu-west-1`, per API Client node.
- **Multi-key management**: store several API keys and switch between them per node.
- **Async and concurrent**: submit and generate tasks in parallel without blocking the queue.
- **Quota guard**: cap image count and video tokens per client to avoid overspend.
- **Clear errors**: readable progress and error messages in the console.

## Nodes

- **Setup**
  - `API Client` (required): creates the ModelArk client used by all other nodes. Pick the key and the region.
  - `Quota Settings`: limit image and video token usage.
- **Image**
  - `Seedream 4`: `seedream-4-5`, `seedream-4-0`. Text-to-image, image editing, multi-reference, group generation. Seedream 4.0 adds a `standard` / `fast` prompt optimization mode.
  - `Seedream 5`: `dola-seedream-5-0-pro`, `dola-seedream-5-0-flash`, `seedream-5-0-lite`.
    - Pro and Flash: 1K / 1.5K / 2K sizes, PNG output, and a transparent background (edit one image with an alpha channel: connect Load Image's `MASK` to `reference_mask`; the output `mask` holds the result's transparency). Pro offers `standard` / `fast` prompt optimization; Flash uses `standard` only.
    - Lite: up to 4K, group generation.
  - `Seedream Layer Decomposition`: splits one image into a base image and up to 16 transparent layers with Seedream 5.0 Pro or Flash. Outputs the base image, each layer placed on the base canvas, per-layer masks, and a JSON list of layer names, descriptions and bounding boxes. The original layer PNGs are saved to the output folder.
- **Video**
  - `Seedance 1.0`: `seedance-1-0-pro`, `seedance-1-0-pro-fast`. Text-to-video and first/last-frame image-to-video.
  - `Seedance 1.5 Pro`: `seedance-1-5-pro`, with audio and draft mode.
  - `Seedance 2 / 2.5`: `dreamina-seedance-2-0` (up to 4K), `-2-0-fast`, `-2-0-mini`, `dreamina-seedance-2-5` (up to 1080p and 30 s), and `dreamina-seedance-2-5-premium` (4K final output only, up to 30 s; whitelist-only). Multimodal reference (image, video, audio), video editing and extension. The 2.5 models add `task_type` (auto / reference / edit / extend), `output_format` (mp4 / mov) and [draft mode](#draft-mode).
  - `Video Query Tasks`: query generation task history.
- **Asset library** (Dreamina Seedance Advanced Creation Rights; see [Virtual Portraits](#virtual-portraits))
  - `Virtual Portrait Asset`: adds an authorized portrait to your private asset library and outputs its `asset://` URI.
  - `Asset Library`: lists your assets (virtual portraits or verified real people) as `asset://` URIs.
- **Understanding**
  - `Visual Understanding`: image and video Q&A with `dola-seed-2-1-turbo`, `seed-2-0-pro` / `lite` / `mini`, `seed-1-8`, `seed-1-6` or `seed-1-6-flash`, multi-turn and deep thinking.
- **Speech** (Seed Speech; needs a [Seed Speech API key](#seed-speech-api-key), not the ModelArk key)
  - `Speech Client` (required for the speech nodes): picks the Seed Speech API key.
  - `Seed Audio 1.0`: `seed-audio-1.0`. Speech, voiceovers and sound effects up to 120 s from a natural-language prompt in 21 languages. Up to three reference voices (an uploaded clip from `Load Audio` up to 30 s, a speaker ID or cloned voice ID, or an audio URL), referred to as `@Audio1`–`@Audio3` in the prompt, or one reference image (`Load Image` or a URL). Uploaded clips and images are sent inline; nothing goes through external storage. Output as wav, mp3, ogg_opus or pcm at a chosen sample rate; speed, volume and pitch controls; sentence/word subtitles; audible watermark and implicit (metadata) watermark.
  - `Seed Voice Clone`: upload a 10–15 s reference clip (`Load Audio`) to train a cloned voice (Voice Replication 2.0) into a voice slot (`S_…`, bought in the Seed Speech console) or a postpaid custom voice ID. Waits until the voice is ready and outputs its `speaker_id` (connect it to TTS `custom_speaker_id` with model `seed-icl-2.0`, or to a Seed Audio reference slot) plus a demo clip. Each slot can be trained 15 times; the first TTS call with the voice starts the slot's billing.
  - `Seed Speech TTS`: text to speech with the TTS 2.0 voice list (`seed-tts-2.0`), TTS 1.0 speaker IDs (`seed-tts-1.0`) or cloned voices (`seed-icl-2.0` / `seed-icl-1.0`). Style instructions (`context_text`), emotion and intensity, speed, volume, pitch, sample rate, language, trailing silence and subtitles/timestamps. Advanced: language detection, context language, Markdown/emoji/LaTeX/parentheses handling, unsupported-language threshold, 1-hour cache and tone fidelity for cloned voices.
  - `Seed Speech ASR`: speech to text in 50+ languages from an uploaded clip (`Load Audio`) or a public audio URL. `seed-asr-fast` sends the clip inline; the standard models (`seed-asr-2.0` / `1.0`, up to 5 h) only take URLs, so a connected clip is uploaded to Comfy.org storage first (Comfy.org login required, like Seedance reference videos). A context image (`Load Image`) is uploaded the same way. Punctuation, number formatting, filler-word removal, speaker labels and hotwords. Advanced: dialogue/scene context and an image for visual context (ASR 2.0), automatic language detection and per-utterance language labels, stereo channel split, silence-based segmentation, Traditional Chinese output and sensitive-word filtering. Outputs the transcript, utterance timings and SRT subtitles.

Model names map to dated model IDs in [`nodes/models_config.py`](./nodes/models_config.py). Activate each model in the ModelArk console for the region you use.

Example workflows are in [`example_workflows/`](./example_workflows).

## Installation

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/byteplus-sa/ComfyUI-BytePlus-ModelArk
pip install -r ComfyUI-BytePlus-ModelArk/requirements.txt
```

Run `pip` with the same Python that runs ComfyUI, then restart ComfyUI. ComfyUI-Manager installs `requirements.txt` for you. If the BytePlus SDK (`byteplus-python-sdk-v2`) is missing or too old, the BytePlus nodes are not loaded and the console prints the exact install command.

## Configure Your API Key

1. Create an API key in the [ModelArk console](https://ai.byteplus.com/ark/region:ap-southeast-1/apikey) and activate the models you plan to use. Keys and model activation are per region.
2. Either:
   - Copy `api_keys.json.example` to `api_keys.json` and add your key, or
   - Add the `API Client` node, choose **Custom** in `key_name`, paste your key and set `new_key_name`. After the first run the key is saved to `api_keys.json`, the node switches to the saved name, and the pasted key is cleared.
3. In the `API Client` node, set `region` to the region the key belongs to.

While `key_name` is **Custom**, the raw key is part of the workflow and of the prompt metadata that ComfyUI embeds in saved images and videos. Save the key under a name (or use `api_keys.json`) before sharing workflows or outputs. Never commit `api_keys.json`.

### Seed Speech API Key

Seed Speech (Seed Audio, TTS, ASR) is a separate BytePlus product with its own API key; ModelArk keys are rejected (`Invalid X-Api-Key`).

1. In the [Seed Speech console](https://console.byteplus.com/voice/new/overview?projectName=default), activate the services you plan to use (trial or paid), then create a key under **Settings → API Keys**.
2. Add the `Speech Client` node and either choose **Custom**, paste the key and set `new_key_name` (saved to `speech_api_keys.json`, then cleared from the node like the ModelArk key), or set the `BYTEPLUS_SEED_SPEECH_API_KEY` environment variable and choose **Environment**.

Seed Speech runs in `ap-southeast-1` (Singapore) only. Never commit `speech_api_keys.json`.

### Reference Videos

Seedance accepts reference videos only as URLs. The `Seedance 2 / 2.5` node handles this two ways:

- **Local videos** connected to `ref_video` inputs are uploaded to Comfy.org storage first. This requires being **logged in to a Comfy.org account** (or a Comfy.org API key) in ComfyUI, and does not work when ComfyUI runs with `--disable-api-nodes`. Uploaded files are deleted after about 24 hours; the plugin reuses an upload for up to 12 hours.
- **Links**: put public `mp4`/`mov` URLs or `asset://<ASSET_ID>` references from the ModelArk asset library in `ref_video_urls`, one per line. Nothing is uploaded.

Reference images and audio can also be given as links: `ref_image_urls` and `ref_audio_urls` take HTTPS URLs or `asset://<ASSET_ID>`, one per line.

### Virtual Portraits

With **Dreamina Seedance Advanced Creation Rights**, you can generate Seedance 2.5 videos featuring a real person from an authorized portrait kept in your private asset library:

1. `API Client` → `Virtual Portrait Asset`: connect the portrait image (or set `image_url` to a public HTTPS URL) and set `group_name` (one virtual-portrait group per person; created if missing) or an existing `group_id`. The node registers the image with `CreateAsset`, waits until it is **Active**, and outputs `asset://<asset_id>`. Running it again with the same image reuses the asset instead of creating a duplicate.
2. Connect `asset_uri` to `ref_image_urls` on `Seedance 2 / 2.5` and refer to it in the prompt by position, for example *"Image 1 is Neon. …"*.

Already have assets? Use `Asset Library` to list them (virtual portraits, or `LivenessFace` groups for people verified in the ModelArk console) and feed `asset_uris` into `ref_image_urls`. Seedance only needs the API key to use an existing `asset://` reference.

Managing assets uses the signed ModelArk OpenAPI, which needs **IAM AK/SK** with asset-library permission, not the API key. Add them to the key's entry in `api_keys.json`, or set `BYTEPLUS_ACCESS_KEY` / `BYTEPLUS_SECRET_KEY` (and `BYTEPLUS_SESSION_TOKEN` for STS keys) before starting ComfyUI:

```json
{"customName": "My key", "apiKey": "…", "accessKey": "AKLT…", "secretKey": "…"}
```

`CreateAsset` needs an HTTPS URL, so a connected image is uploaded to Comfy.org storage first (Comfy.org login required, like reference videos). CreateAsset is rate-limited by your Advanced Creation Rights tier (Entry 3, Advanced 120, Premium 300 requests per minute). Only use portraits you are authorized to use.

### Draft Mode

Seedance 1.5 Pro and the Seedance 2.5 models can render a quick 480p draft before the full-quality video:

1. Enable `draft_mode` and run. The draft task ID is shown in the response and remembered by the node.
2. To render the final video, keep `draft_mode` on and either enable `reuse_last_draft_task` (uses the draft this node made, until ComfyUI restarts) or paste the draft task ID(s) into `draft_task_id`. Set the final `resolution` and run again. The final video reuses the draft's prompt, references, duration, aspect ratio, seed and audio setting.

`draft_task_id` is ignored when `draft_mode` is off or `reuse_last_draft_task` is on.

Final videos from a Seedance 2.5 draft are 1080p; from a 2.5 Premium draft, 4K. Draft task IDs are valid for 7 days.

## Development

```bash
python -m unittest tests.test_workflow_templates
```

`tests/test_model_updates.py` needs a ComfyUI checkout and a Python environment with torch and the BytePlus SDK; it is skipped otherwise:

```bash
COMFYUI_ROOT=/path/to/ComfyUI python -m unittest tests.test_model_updates
```

## Roadmap

- [x] Region selection (`ap-southeast-1` default, `eu-west-1`)
- [x] BytePlus model IDs (`dreamina-seedance-*`, `dola-seedream-*`, `seed-*`)
- [x] BytePlus capability limits (Seedance 2.5 at 1080p, 30 s, mp4/mov output, task types)
- [x] English UI strings and BytePlus console guidance in error messages
- [ ] Optional reference video upload via your own object storage (TOS or S3, presigned URL)
- [x] Seedance 2.5 / 2.5 Premium draft mode
- [x] Seedream 5.0 Pro / Flash layer decomposition and transparent backgrounds
- [x] `asset://` references for images, videos and audio; Virtual Portrait asset library nodes
- [x] Seedream 5.0 Flash
- [x] Seed Speech: Seed Audio 1.0, TTS and ASR

## Compatibility

Supports ComfyUI Classic Canvas and Nodes 2.0. Minimum ComfyUI `0.25.1`.

## License

[MIT](./LICENSE)
