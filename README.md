# ComfyUI BytePlus ModelArk

ComfyUI custom nodes for **BytePlus ModelArk**: Seedance video generation, Seedream image generation, and Seed multimodal understanding.

Calls go directly to ModelArk with **your own ARK API key**, so usage is billed to your BytePlus account (including contract pricing and resource packs). No Comfy credits or third-party proxy involved.

> **Status: in development (v0.1.0).** BytePlus region support and BytePlus model IDs are being wired in. Until then, some nodes may still target default Ark settings. See [Roadmap](#roadmap).

## Features

- **Multi-key management**: store several API keys and switch between them per node.
- **Async and concurrent**: submit and generate tasks in parallel without blocking the queue.
- **Quota guard**: cap image count and video tokens per client to avoid overspend.
- **Clear errors**: readable progress and error messages in the console.

## Nodes

- **Setup**
  - `API Client` (required): creates the ModelArk client used by all other nodes.
  - `Quota Settings`: limit image and video token usage.
- **Image**
  - `Seedream 3`, `Seedream 4 / 4.5`, `Seedream 5.0 Pro / Lite`: text-to-image, image editing, multi-reference, batch generation.
- **Video**
  - `Seedance 1.0 / 1.5 Pro`: text-to-video and first/last-frame image-to-video.
  - `Seedance 2.0 / 2.5`: multimodal reference (image, video, audio), video editing and extension.
  - `Task List`: query and manage generation task history.
- **Understanding**
  - `Visual Understanding`: image and video Q&A with Seed models, multi-turn and deep thinking.

Example workflows are in [`example_workflows/`](./example_workflows).

## Installation

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/byteplus-sa/ComfyUI-BytePlus-ModelArk
pip install -r ComfyUI-BytePlus-ModelArk/requirements.txt
```

Restart ComfyUI.

## Configure Your API Key

1. Create an API key in the [ModelArk console](https://ai.byteplus.com/ark/region:ap-southeast-1/apikey) and activate the models you plan to use.
2. Either:
   - Rename `api_keys.json.example` to `api_keys.json` and add your key, or
   - Add the `API Client` node, choose **Custom** in `key_name`, and paste your key. Set `new_key_name` to save it for later (refresh the browser to see it in the list).

Never commit `api_keys.json` or share workflows that contain keys.

### Reference Videos

Seedance accepts reference videos only as a URL, so local videos are uploaded to Comfy.org storage first. This requires being **logged in to a Comfy.org account** in ComfyUI. Uploaded files are deleted automatically after 24 hours. You can also pass a video URL you already host.

## Roadmap

- [ ] Region selection (`ap-southeast-1` default, `eu-west-1`)
- [ ] BytePlus model IDs (`dreamina-seedance-*`, `dola-seedream-*`, `seed-*`)
- [ ] BytePlus capability limits (for example Seedance 2.5 at 1080p, mp4/mov)
- [ ] Optional reference video upload via your own object storage (TOS or S3, presigned URL)
- [ ] `asset://` support for ModelArk asset library (virtual and verified real-person assets)
- [ ] English UI strings and BytePlus console links in error messages

## Compatibility

Supports ComfyUI Classic Canvas and Nodes 2.0. Minimum ComfyUI `0.25.1`.

## License

[MIT](./LICENSE)
