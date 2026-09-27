# ComfyUI BytePlus ModelArk

ComfyUI custom nodes for **BytePlus ModelArk**: Seedance video generation, Seedream image generation, and Seed multimodal understanding.

Generation calls go directly to ModelArk with **your own ModelArk API key**, so usage is billed to your BytePlus account (including contract pricing and resource packs). No Comfy credits are used. The one exception is local reference videos for Seedance 2 / 2.5, which pass through Comfy.org storage (see [Reference Videos](#reference-videos)).

> **Status: in development (v0.2.0).** Nodes target BytePlus ModelArk regions and model IDs. See [Roadmap](#roadmap).

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
  - `Seedream 5`: `dola-seedream-5-0-pro`, `seedream-5-0-lite`.
    - Pro: 1K / 1.5K / 2K sizes, `standard` / `fast` prompt optimization, PNG output, and a transparent background (edit one image with an alpha channel: connect Load Image's `MASK` to `reference_mask`; the output `mask` holds the result's transparency).
    - Lite: up to 4K, group generation.
  - `Seedream Layer Decomposition`: splits one image into a base image and up to 16 transparent layers with Seedream 5.0 Pro. Outputs the base image, each layer placed on the base canvas, per-layer masks, and a JSON list of layer names, descriptions and bounding boxes. The original layer PNGs are saved to the output folder.
- **Video**
  - `Seedance 1.0`: `seedance-1-0-pro`, `seedance-1-0-pro-fast`. Text-to-video and first/last-frame image-to-video.
  - `Seedance 1.5 Pro`: `seedance-1-5-pro`, with audio and draft mode.
  - `Seedance 2 / 2.5`: `dreamina-seedance-2-0` (up to 4K), `-2-0-fast`, `-2-0-mini`, `dreamina-seedance-2-5` (up to 1080p and 30 s), and `dreamina-seedance-2-5-premium` (up to 4K and 30 s; whitelist-only). Multimodal reference (image, video, audio), video editing and extension. The 2.5 models add `task_type` (auto / reference / edit / extend), `output_format` (mp4 / mov) and [draft mode](#draft-mode).
  - `Video Query Tasks`: query generation task history.
- **Understanding**
  - `Visual Understanding`: image and video Q&A with `dola-seed-2-1-turbo`, `seed-2-0-pro` / `lite` / `mini`, `seed-1-8`, `seed-1-6` or `seed-1-6-flash`, multi-turn and deep thinking.

Model names map to dated model IDs in [`nodes/models_config.py`](./nodes/models_config.py). Activate each model in the ModelArk console for the region you use.

Example workflows are in [`example_workflows/`](./example_workflows).

## Installation

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/byteplus-sa/ComfyUI-BytePlus-ModelArk
pip install -r ComfyUI-BytePlus-ModelArk/requirements.txt
```

Restart ComfyUI. If the BytePlus SDK (`byteplus-python-sdk-v2`) is missing, the plugin installs `requirements.txt` on first load.

## Configure Your API Key

1. Create an API key in the [ModelArk console](https://ai.byteplus.com/ark/region:ap-southeast-1/apikey) and activate the models you plan to use. Keys and model activation are per region.
2. Either:
   - Copy `api_keys.json.example` to `api_keys.json` and add your key, or
   - Add the `API Client` node, choose **Custom** in `key_name`, and paste your key. Set `new_key_name` to save it for later (refresh the browser to see it in the list).
3. In the `API Client` node, set `region` to the region the key belongs to.

Never commit `api_keys.json` or share workflows that contain keys.

### Reference Videos

Seedance accepts reference videos only as URLs. The `Seedance 2 / 2.5` node handles this two ways:

- **Local videos** connected to `ref_video` inputs are uploaded to Comfy.org storage first. This requires being **logged in to a Comfy.org account** (or a Comfy.org API key) in ComfyUI, and does not work when ComfyUI runs with `--disable-api-nodes`. Uploaded files are deleted after about 24 hours; the plugin reuses an upload for up to 12 hours.
- **Links**: put public `mp4`/`mov` URLs or `asset://<ASSET_ID>` references from the ModelArk asset library in `ref_video_urls`, one per line. Nothing is uploaded.

### Draft Mode

Seedance 1.5 Pro and the Seedance 2.5 models can render a quick 480p draft before the full-quality video:

1. Enable `draft_mode` and run. The draft task ID is shown in the response and remembered by the node.
2. To render the final video, either enable `reuse_last_draft_task` or paste the draft task ID(s) into `draft_task_id`, set the final `resolution`, and run again. The final video reuses the draft's prompt, references, duration, aspect ratio, seed and audio setting.

Final videos from a Seedance 2.5 draft are 1080p; from a 2.5 Premium draft, 1080p or 4K. Draft task IDs are valid for 7 days.

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
- [x] Seedream 5.0 Pro layer decomposition and transparent backgrounds
- [ ] `asset://` inputs for reference images and audio (videos already accept `asset://` links)
- [ ] Seedream 5.0 Flash (not yet in the ModelArk catalog for this account)

## Compatibility

Supports ComfyUI Classic Canvas and Nodes 2.0. Minimum ComfyUI `0.25.1`.

## License

[MIT](./LICENSE)
