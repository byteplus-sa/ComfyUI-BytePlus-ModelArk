---
name: comfyui-release-and-testing
description: Test this node pack and maintain its example workflows. Use before committing node schema changes, when a template test fails, or when a release candidate needs node tests, workflow version checks, or a manual ComfyUI smoke test. Covers unittest setup, Ark SDK fakes, positional widget values, DynamicCombo inputs, and template thumbnails.
---

# Testing, templates and releases

Registry/CLI facts verified 2026-09-27 against docs.comfy.org/registry, Comfy-Org/comfy-cli v1.21.0, Comfy-Org/publish-node-action, ComfyUI v0.37.0 and ComfyUI_frontend `main`.

## 1. Run the tests

```bash
# Always: template/workflow JSON checks (stdlib only, no ComfyUI)
python3 -m unittest tests.test_workflow_templates -v

# Node behaviour: needs a ComfyUI checkout + an interpreter with torch,
# ComfyUI's requirements and this pack's requirements.txt
COMFYUI_ROOT=/path/to/ComfyUI /path/to/python -m unittest tests.test_model_updates -v
```

Without `COMFYUI_ROOT` the node tests (60 as of 2026-09-27) report **skipped**, not passed — say so when reporting results. The local portable install's `standalone-env` python lacks torch and the Ark SDK, so it can't run them as-is; don't pip-install into the user's ComfyUI env without asking.

Writing node tests (`tests/test_model_updates.py` pattern):
- The plugin is loaded as a synthetic package (`byteplus_plugin_test`) after putting ComfyUI on `sys.path`.
- Fake the Ark client with `types.SimpleNamespace` objects exposing the SDK methods you exercise (`content_generation.tasks.create/get/delete`, `images.generate`, `responses.create`, `files.create`).
- Swap `executor.PromptServer.instance` for a stub with `send_sync`; stub `sys.modules["comfy_api_nodes.util"]` for the Comfy.org upload; patch `cls.hidden` where `execute` reads it.
- Call classmethods directly: `asyncio.run(Node.execute(...))`. Never hit the real ModelArk API in tests.

## 2. Example workflows (`example_workflows/`)

They appear in ComfyUI's template browser under the pack's folder name; the title is the file basename.

- **Thumbnail must be `<same basename>.jpg`** — the frontend only requests `.jpg` for custom-node templates. The old thumbnails (pre-retarget Chinese/Jimeng UI) were deleted; regenerate by loading each template in an English ComfyUI and taking a screenshot of the graph.
- Format is the UI export, schema `version: 0.4` (tested). Every BytePlus node needs `properties.cnr_id == "ComfyUI-BytePlus-ModelArk"` (must equal `[project].name`) and `properties.ver == <pyproject version>`.
- `widgets_values` is **positional**. Adding/removing/reordering a widget — including the hidden `control_after_generate` value after a `seed` — shifts every later value. DynamicCombo nodes serialize children as `model_version.<name>` and the set changes with the selected model.
- Best way to update after a schema change: load the old workflow in a live ComfyUI with the new code, fix values, **re-export**, then update `tests/test_workflow_templates.py` expected orders (`CURRENT_INPUT_ORDERS`, `SEEDANCE2_INPUT_ORDERS`, index checks).
- Keep the API Client key widget empty, no third-party nodes (rgthree/pysssss/Note), no CJK / "Jimeng" / "doubao" text — all tested.
- New template file → add it to `EXPECTED_WORKFLOWS` in the test. `BytePlusSeedreamLayers` has no template yet.
- Optional offline check: `comfy workflow validate --workflow <api-format.json> --input object_info.json` (comfy-cli) validates class types, inputs, enums and wiring against a saved `/object_info` dump.

## 3. Manual smoke test in ComfyUI

1. Symlink or clone the pack into `ComfyUI/custom_nodes/`, restart ComfyUI (new/changed Python needs a restart; JS needs only a hard browser reload). A throwaway instance: `python main.py --base-directory <scratch dir> --port 8201 --cpu --disable-auto-launch`. ⚠️ On first start with a fresh base directory ComfyUI **moves** the install's `user/comfyui.db` into it ("Renamed legacy database …") — if the install is someone's real ComfyUI, rename `comfyui.db.bak` back afterwards, or pre-create `<base>/user/comfyui.db`.
2. Startup log: no "Error while calling comfy_entrypoint" / "Cannot import" for the pack.
3. `curl -s localhost:8188/object_info/BytePlusSeedance2 | head` — node registered with expected inputs.
4. Load each changed example workflow in **Classic Canvas and Nodes 2.0**; check widget visibility rules, values, and that a run shows progress and can be cancelled (Cancel should delete pending ModelArk tasks).

## 4. Version bump checklist

1. `pyproject.toml` `version` (semver; every registry publish needs a new, never-reused version — published versions are immutable, only deprecatable).
2. `properties.ver` on every BytePlus node in every `example_workflows/*.json` (test enforces it).
3. README "Status" line and changelog notes.
4. Run `python3 -m unittest tests.test_workflow_templates`.

## 5. Publishing

- `.github/workflows/publish_action.yml` is **manual** (`workflow_dispatch`) and both jobs run only on `main`.
- It uses `Comfy-Org/publish-node-action@main` with secret `REGISTRY_ACCESS_TOKEN`; the action runs `comfy node publish` and publishes **git-tracked files only**. Dev files are excluded by `.comfyignore` (gitignore syntax): `.github/`, `.agents/`, `.claude/`, `tests/`, `CLAUDE.md`, `AGENTS.md`. `api_keys.json.example` must ship (README setup step).
- Local equivalent: `comfy node validate`, `comfy node pack` (inspect `node.zip`), `comfy node publish --token …`.
- `[project].name` is the immutable registry id and the install folder name for registry installs.
- `pyproject.toml` declares `requires-python`, `classifiers = ["Operating System :: OS Independent"]` (API-only, no GPU classifier), `Issues` URL (comfy-cli reads only Homepage/Documentation/Repository/Issues), `requires-comfyui = ">=0.31.0"` (keep in sync with README), and a repository-hosted `[tool.comfy] Icon` (https URL, square ≤ 400 px).
- Keep `requirements.txt` and `[project].dependencies` identical: ComfyUI-Manager installs from `requirements.txt`; `[project].dependencies` is registry metadata. Never list torch/numpy/Pillow/aiohttp (ComfyUI ships them).

## 6. Registry standards

New versions start `pending` → automated scan → `active`, or `flagged` for human review (ComfyUI-Manager refuses to install a flagged version unless ComfyUI listens only on localhost or the user sets `allow_flagged_nodepack_install`). Written rules: no `eval`/`exec`, **no runtime `pip install` via subprocess**, no obfuscation, don't interfere with other packs. `comfy node publish` also runs `ruff --select S102,S307,E702` (warnings today, "error soon"). `__init__.py` `check_dependencies()` only checks the SDK and prints the install command — keep it that way.

## References

- Official Comfy agent skill for custom nodes (bundled with comfy-cli): https://github.com/Comfy-Org/comfy-cli/blob/main/comfy_cli/skills/comfy-custom-nodes/SKILL.md
- https://docs.comfy.org/registry/specifications · /registry/publishing · /registry/standards · /custom-nodes/workflow_templates
