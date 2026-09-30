# ComfyUI BytePlus ModelArk

ComfyUI custom nodes for **BytePlus ModelArk**: Seedance video generation, Seedream image generation, and Seed multimodal understanding. Plus **BytePlus Seed Speech**: Seed Audio 1.0 generation, text to speech (TTS) and speech recognition (ASR).

Generation calls go directly to ModelArk with **your own ModelArk API key**, so usage is billed to your BytePlus account (including contract pricing and resource packs). No Comfy credits are used. Seed Speech nodes use a separate Seed Speech API key (see [Seed Speech API Key](#seed-speech-api-key)). The one exception is local reference videos for Seedance 2 / 2.5 (and media for new private assets), which pass through Comfy.org storage (see [Reference Videos](#reference-videos)).

> **Status: in development (v0.3.0).** Nodes target BytePlus ModelArk regions and model IDs. See [Roadmap](#roadmap).

**v0.3.0:** The image, video, understanding and asset nodes now have the same layout as ComfyUI's built-in ByteDance nodes (same node split, inputs, defaults and outputs), plus an `API Client` input and this pack's extras under advanced inputs. The previous nodes stay available as "(Legacy)" so saved workflows still load. Also adds Seed Speech nodes: `Seed Audio 1.0`, `Seed Speech TTS`, `Seed Speech ASR`, `Seed Voice Clone` and the `Speech Client` that holds the Seed Speech API key. Requires ComfyUI 0.31.0 or later.

**v0.2.2:** Seedance 2.5 Premium accepts 4K normal renders only; draft mode submits at 480p. Unsupported normal resolutions are rejected before submission.

## Features

- **Region selection**: `ap-southeast-1` (default) or `eu-west-1`, per API Client node.
- **Multi-key management**: store several API keys and switch between them per node.
- **Async and concurrent**: submit and generate tasks in parallel without blocking the queue.
- **Quota guard**: cap image count and video tokens per client to avoid overspend.
- **Clear errors**: readable progress and error messages in the console.

## Nodes

The generation nodes match ComfyUI's built-in ByteDance nodes input for input, so workflows look the same as with Comfy's partner nodes. The differences: every node takes the `API Client` as its first input (calls go to ModelArk with your key), BytePlus model IDs are used, and this pack's extras (parallel generations, saving to the output folder, non-blocking runs, and so on) sit under advanced inputs after the built-in ones.

- **Setup**
  - `API Client` (required): creates the ModelArk client used by all other nodes. Pick the key and the region.
  - `Quota Settings`: limit image and video token usage.
- **Image**
  - `Seedream 4.5 & 5.0`: Seedream 5.0 Pro, 5.0 Flash, 5.0 Lite, 4.5 and 4.0 in one node. Size presets per model (plus the ModelArk "adaptive" resolution levels), up to 10 reference images (14 on 5.0 Lite), `max_images` for related image sets (Lite, 4.5, 4.0), prompt optimization and "thinking" where the model supports them. Advanced: parallel generations, PNG output and a transparent background on Pro and Flash (connect Load Image's `MASK` to `reference_mask`; the `mask` output holds the result's transparency).
  - `Seedream 5.0 Layer Separation`: splits one image into a base image and up to 16 transparent layers with Seedream 5.0 Pro or Flash. Outputs the base image and mask, the layers and their masks, bounding boxes, a `layer_stack` for Create Layered Image, and a JSON list of layer names and descriptions.
- **Video**
  - `Seedance Text to Video`, `Seedance Image to Video`, `Seedance First-Last-Frame to Video`: `seedance-1-5-pro`, `seedance-1-0-pro`, `seedance-1-0-pro-fast` (First-Last-Frame: 1.5 Pro and 1.0 Pro). 480p–1080p, 3–12 s (1.5 Pro: 4–12 s), `camera_fixed`, `watermark`, and `generate_audio` on 1.5 Pro. Advanced: auto duration and 480p [draft mode](#draft-mode) (1.5 Pro), offline inference, parallel generations, non-blocking runs.
  - `Seedance 2.5 Text to Video`, `Seedance 2.5 First-Last-Frame to Video`, `Seedance 2.5 Reference to Video`: Seedance 2.5 (up to 1080p and 30 s), 2.5 Premium (4K, whitelist-only), 2.0 (up to 4K), 2.0 Fast and 2.0 Mini, each with a *Draft* option for 2.5 and 2.5 Premium. Reference to Video takes up to 9 images, 3 videos and 3 audio clips on 2.0 (30 / 10 / 10 on 2.5), plus `asset_N` inputs for [links and assets](#reference-links-and-assets), with `task_type` (auto / reference / edit / extend) and optional down/upscaling of reference videos. `output_format` (mp4 / mov) on 2.5.
  - `Seedance Draft to Final Video`: renders the final video of a draft (see [Draft Mode](#draft-mode)).
  - `Video Query Tasks`: query generation task history.
- **Asset library** (Dreamina Seedance Advanced Creation Rights; see [Private Assets](#private-assets))
  - `Create Image Asset`, `Create Video Asset`, `Create Audio Asset`: add media (a connected input or an HTTPS link) to an asset group in your private asset library and output its `asset_id`, `group_id` and `asset://` URI.
  - `Asset Library`: lists your assets (virtual portraits or verified real people) as `asset://` URIs.
- **Understanding**
  - `Seed`: text answers with Seed 2.0 Pro / Lite / Mini, Seed 2.1 Turbo, Seed 1.8, Seed 1.6 or Seed 1.6 Flash, with up to 20 images and 4 videos as context, temperature and a system prompt. Advanced: image detail, video fps, deep thinking and effort, multi-turn conversations, streaming; a second output returns the raw response JSON.
- **Speech** (Seed Speech; needs a [Seed Speech API key](#seed-speech-api-key), not the ModelArk key)
  - `Speech Client` (required for the speech nodes): picks the Seed Speech API key.
  - `Seed Audio 1.0`: `seed-audio-1.0`. Speech, voiceovers, music and sound effects up to 120 s from a natural-language prompt in 20 languages, laid out like ComfyUI's built-in Seed Audio node. `reference_mode`: text only; audio reference (up to three clips from `Load Audio` up to 30 s, or speaker IDs, cloned voice IDs or audio URLs, referred to as `@Audio1`–`@Audio3` in the prompt); image reference (`Load Image` or a URL); or a preset TTS 2.0 voice. Sample rate, speed, loudness and pitch controls. Advanced: output format (wav, mp3, ogg_opus, pcm), sentence/word subtitles, audible and metadata watermarks. Uploaded clips and images are sent inline.
  - `Seed Voice Clone`: upload a 10–15 s reference clip (`Load Audio`) to train a cloned voice (Voice Replication 2.0) into a voice slot (`S_…`, bought in the Seed Speech console) or a postpaid custom voice ID. Waits until the voice is ready and outputs its `speaker_id` (connect it to TTS `custom_speaker_id` with model `seed-icl-2.0`, or to a Seed Audio reference slot) plus a demo clip. Each slot can be trained 15 times; the first TTS call with the voice starts the slot's billing.
  - `Seed Speech TTS`: text to speech with the TTS 2.0 voice list (`seed-tts-2.0`), TTS 1.0 speaker IDs (`seed-tts-1.0`) or cloned voices (`seed-icl-2.0` / `seed-icl-1.0`). Style instructions (`context_text`), emotion and intensity, speed, volume, pitch, sample rate, language, trailing silence and subtitles/timestamps. Advanced: language detection, context language, Markdown/emoji/LaTeX/parentheses handling, unsupported-language threshold, 1-hour cache and tone fidelity for cloned voices.
  - `Seed Speech ASR`: speech to text in 50+ languages from an uploaded clip (`Load Audio`) or a public audio URL. `seed-asr-fast` sends the clip inline; the standard models (`seed-asr-2.0` / `1.0`, up to 5 h) only take URLs, so a connected clip is uploaded to Comfy.org storage first (Comfy.org login required, like Seedance reference videos). A context image (`Load Image`) is uploaded the same way. Punctuation, number formatting, filler-word removal, speaker labels and hotwords. Advanced: dialogue/scene context and an image for visual context (ASR 2.0), automatic language detection and per-utterance language labels, stereo channel split, silence-based segmentation, Traditional Chinese output and sensitive-word filtering. Outputs the transcript, utterance timings and SRT subtitles.

Model names map to dated model IDs in [`nodes/models_config.py`](./nodes/models_config.py). Activate each model in the ModelArk console for the region you use.

**Legacy nodes.** `Seedream 4`, `Seedream 5`, `Seedream Layer Decomposition`, `Seedance 1.0`, `Seedance 1.5 Pro`, `Seedance 2 / 2.5`, `Virtual Portrait Asset` and `Visual Understanding` are marked "(Legacy)": they still load and run in saved workflows but are hidden from node search. Use the nodes above for new workflows.

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

Seedance accepts reference videos only as URLs. Videos connected to `Reference to Video` (`video_N`) are uploaded to Comfy.org storage first. This requires being **logged in to a Comfy.org account** (or a Comfy.org API key) in ComfyUI, and does not work when ComfyUI runs with `--disable-api-nodes`. Uploaded files are deleted after about 24 hours; the plugin reuses an upload for up to 12 hours. To skip the upload, pass a link or an asset instead (below).

Connected images and audio are sent inline; nothing is uploaded.

### Reference Links and Assets

`Reference to Video` has `asset_N` inputs, and `First-Last-Frame to Video` has `first_frame_asset_id` / `last_frame_asset_id`. Each takes one of:

- an asset ID or `asset://<ASSET_ID>` from your private asset library (for example the `asset_id` output of `Create Image Asset`), or
- a public `https://` link to an image, video or audio file. Type it into a String node (or any text output) and connect it.

For `asset_N`, the node needs to know whether each entry is an image, video or audio: links are recognized by their file extension (or the server's content type), and asset IDs are looked up in the asset library, which needs IAM AK/SK (see [Private Assets](#private-assets)). In the prompt, refer to references by position (*Image 1*, *Video 1*, …): connected inputs come first, then `asset_N` entries. You can also write `asset1`, `asset2`, … and the node replaces it with the matching position.

### Private Assets

With **Dreamina Seedance Advanced Creation Rights**, you can keep authorized media, such as virtual portraits or verified real people, in a private asset library and use it in Seedance 2 / 2.5:

1. `API Client` → `Create Image Asset` (or `Create Video Asset` / `Create Audio Asset`): connect the media, or set the URL input to a public HTTPS link. Set an existing `group_id` (for example a real-person group created in the ModelArk console), or leave it empty and set `group_name` to find or create a virtual-portrait group. The node registers the media with `CreateAsset`, waits until it is **Active**, and outputs `asset_id`, `group_id` and `asset_uri`. Running it again with the same image reuses the asset instead of creating a duplicate.
2. Connect `asset_id` to an `asset_N` input of `Seedance 2.5 Reference to Video` (or to `first_frame_asset_id` on First-Last-Frame).

ComfyUI's built-in Create Asset nodes run real-person verification inside the node. BytePlus offers real-person verification only in the ModelArk console (a QR-code invitation the person scans; see [Add real-human assets](https://ai.byteplus.com/ark/region:ap-southeast-1/docs/upload-real-person-portrait-assets)), so create those groups there and pass their `group_id`.

Already have assets? Use `Asset Library` to list them (virtual portraits, or `LivenessFace` groups for people verified in the ModelArk console). Using an existing asset in Seedance only needs the API key, except that `asset_N` looks up the asset's type with AK/SK.

Managing assets uses the signed ModelArk OpenAPI, which needs **IAM AK/SK** with asset-library permission, not the API key. Add them to the key's entry in `api_keys.json`, or set `BYTEPLUS_ACCESS_KEY` / `BYTEPLUS_SECRET_KEY` (and `BYTEPLUS_SESSION_TOKEN` for STS keys) before starting ComfyUI:

```json
{"customName": "My key", "apiKey": "…", "accessKey": "AKLT…", "secretKey": "…"}
```

`CreateAsset` needs an HTTPS URL, so a connected image, video or audio clip is uploaded to Comfy.org storage first (Comfy.org login required, like reference videos); a URL input skips the upload. CreateAsset is rate-limited by your Advanced Creation Rights tier (Entry 3, Advanced 120, Premium 300 requests per minute). Only use media you are authorized to use.

### Draft Mode

Seedance 1.5 Pro and the Seedance 2.5 models can render a quick 480p draft before the full-quality video:

1. Pick a *Draft* model option (`Seedance 2.5 Draft`, `Seedance 2.5 Premium Draft`) on a Seedance 2.5 node, or enable `draft_mode` (advanced) on a Seedance 1.x node with 1.5 Pro. Set the seed control to **fixed** and run. The node outputs the draft's `draft_task_id`.
2. Connect `draft_task_id` to `Seedance Draft to Final Video` (or paste IDs into it, one per line) and run again. The draft node is not re-run while its inputs are unchanged, so the final uses the draft you reviewed. The final video reuses the draft's prompt, references, duration, aspect ratio, seed and audio setting.

Final videos from a Seedance 2.5 draft are 1080p; from a 2.5 Premium draft, 4K. Draft to Final reads the model from the draft task, so it needs no model setting. Draft task IDs are valid for 7 days. The Legacy nodes keep their in-node `draft_mode` / `reuse_last_draft_task` flow.

## Development

```bash
python -m unittest tests.test_workflow_templates
```

The node tests need a ComfyUI checkout and a Python environment with torch and the BytePlus SDK; they are skipped otherwise:

```bash
COMFYUI_ROOT=/path/to/ComfyUI python -m unittest tests.test_model_updates tests.test_workflow_templates tests.test_core_style_seedance1 tests.test_core_style_seedance2 tests.test_core_style_seedream tests.test_core_style_seed
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
- [x] Same node layout as ComfyUI's built-in ByteDance nodes

## Compatibility

Supports ComfyUI Classic Canvas and Nodes 2.0. Minimum ComfyUI `0.31.0`.

## License

[MIT](./LICENSE)
