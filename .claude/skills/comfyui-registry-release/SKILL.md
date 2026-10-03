---
name: comfyui-registry-release
description: Prepare, publish, and verify releases of this ComfyUI-BytePlus-ModelArk node pack in the Comfy Registry. Use this skill for version bumps, release packaging, Registry metadata or icon changes, GitHub publish workflow runs, customer update questions, and pending or flagged Registry versions. Check the exact shipped commit, package contents, workflow jobs, and version scan status before calling a release available.
---

# Comfy Registry release

Run from the repository root. Manage one release at a time and report the shipped commit, tag, Registry version status, and customer availability separately. The current publishing workflow is `.github/workflows/publish_action.yml`; read it before each release because its behavior may change.

## 1. Establish the release boundary

1. Read the repository root `AGENTS.md`, `pyproject.toml`, `.comfyignore`, the publish workflow, and the relevant code and release notes.
2. Check `git status --short`, the current branch, `git log -5 --oneline`, remote `main`, and existing version tags. Confirm that every requested feature commit is an ancestor of the exact commit to be released. Inspect the tagged or committed file when a feature's presence matters; a local uncommitted edit does not ship.
3. Preserve unrelated working-tree changes. If the checkout is dirty, use an isolated checkout from the intended release commit. Include other in-progress changes only when they are part of the authorized release. Do not stash, reset, or overwrite someone else's work to make the tree clean.
4. Choose a new semantic version that is absent from Git tags and the Registry. Published Registry versions are immutable. A metadata or icon change also needs a new version when it must appear in a new package.

Useful checks:

```bash
git status --short
git fetch origin main --tags
git log -5 --oneline
git tag --list 'v*'
git merge-base --is-ancestor <feature-commit> HEAD
```

## 2. Prepare metadata and package inputs

Update these together:

- `[project].version` in `pyproject.toml`.
- `[tool.comfy].DisplayName` and `[project].description` when a service or model family was added or removed. They are the Registry listing's name and description, so keep them consistent with the README title and tagline.
- The README status line and a short release note describing what customers receive.
- `properties.ver` for every `BytePlus` node in every `example_workflows/*.json`. Leave `comfy-core` node versions alone.
- The expected version in `tests/test_workflow_templates.py` if the test still uses a literal.
- Any changed model list, workflow input order, or dependency metadata required by the feature itself. Keep `[project].dependencies` and `requirements.txt` aligned.

For an icon, use `[tool.comfy].Icon` with a stable HTTPS URL to a square SVG, PNG, JPG, or GIF no larger than 400 × 400 pixels. Prefer a repository-hosted copy when the original is on a third-party CDN. Use a fixed image format if the source URL can negotiate WebP or AVIF. Ensure the asset is Git tracked before packing; `comfy node pack` includes tracked files and applies `.comfyignore`. The raw GitHub URL becomes reachable only after the asset is pushed.

Never put Registry tokens, BytePlus keys (ModelArk, Seed Speech, MediaKit, IAM AK/SK), a `.env` file, or upload caches into Git, workflow templates, the package, commands, or logs. The publish action reads `REGISTRY_ACCESS_TOKEN` from GitHub Actions secrets.

## 3. Validate the release candidate

```bash
python3 -m unittest tests.test_workflow_templates -v
COMFYUI_ROOT=/path/to/ComfyUI /path/to/python -m unittest tests.test_credentials tests.test_mediakit \
  tests.test_model_updates tests.test_workflow_templates tests.test_core_style_seedance1 \
  tests.test_core_style_seedance2 tests.test_core_style_seedream tests.test_core_style_seed -v
comfy --skip-prompt node validate
comfy --skip-prompt node pack
unzip -Z -1 node.zip
git diff --check
```

Run node tests with a ComfyUI-compatible interpreter. Without `COMFYUI_ROOT`, skipped tests are not a pass. If a CPU-only interpreter causes ComfyUI to select CUDA, set `comfy.cli_args.args.cpu = True` before discovering the tests; do not change production code to work around the test environment.

Check that the ZIP contains the new icon and required runtime files, and excludes `.agents/`, `.claude/`, `.github/`, tests, credentials, and local caches. Inspect the full scoped diff and scan staged files for credentials without printing any value found. Run configured lint and type checks when applicable. Exercise the changed path: for a metadata-only release, a successful validation and inspected package are the smoke test; for node behavior changes, run the relevant node test and a ComfyUI smoke test when the environment is available. Remove `node.zip` after inspection.

## 4. Publish the exact candidate

Proceed with commit and push only when the user's request authorizes publication and the project rules permit them. Commit only release files. Recheck remote `main` before pushing so a concurrent change cannot silently alter the intended package. The workflow only publishes from `main` and must be dispatched manually.

Trigger `.github/workflows/publish_action.yml` on `main` through GitHub Actions or `gh workflow run publish_action.yml --ref main`. If `gh` authentication fails while Git push works, use an already authorized GitHub credential with `POST /repos/byteplus-sa/ComfyUI-BytePlus-ModelArk/actions/workflows/publish_action.yml/dispatches` and body `{"ref":"main"}`. A successful dispatch may return HTTP 200 or 204; find the run and verify its jobs. Never paste a token into a shell command, source file, or tool output. The Registry publishing key is for Comfy publishing; it may not authorize Registry metadata-edit APIs.

The workflow creates `v{version}` and a GitHub Release, and runs the Comfy publish action. Verify both jobs, the tag's commit, and the release URL. A green workflow means the upload succeeded; it does not prove the Registry security scan has accepted the version.

## 5. Verify customer availability

Check these live endpoints after publishing:

- Node metadata and icon: `https://api.comfy.org/nodes/ComfyUI-BytePlus-ModelArk`
- Every version and scan status: `https://api.comfy.org/nodes/ComfyUI-BytePlus-ModelArk/versions?include_status_reason=true`
- GitHub workflow run, tag, and Release for the exact version.

Treat the node's `NodeStatusActive` separately from the version's status:

| Version status | What to report |
|---|---|
| `NodeVersionStatusPending` | Uploaded; scan or review is still running. Do not call it available in Manager yet. |
| `NodeVersionStatusActive` | Available for customers to install or update through the Registry path, subject to Manager index refresh. |
| `NodeVersionStatusFlagged` | Inspect `status_reason` (it may be a JSON string), summarize findings and affected files, and pursue legitimate fixes or manual review. Do not hide required network or credential handling merely to evade scanning. |

The v0.2.1 icon release demonstrated why this distinction matters: its publish job succeeded while the version was still pending; v0.2.0 had been flagged for informational findings in API networking and environment access. Recheck current status rather than assuming those historical results still apply. If an install command says the node is not found, compare Registry version status with the Manager index before diagnosing the package itself. Tell customers to update through ComfyUI Manager and restart ComfyUI once the new version is active.

## Sources

- [ComfyUI metadata and icon specification](https://docs.comfy.org/registry/specifications)
- [ComfyUI publishing and `.comfyignore`](https://docs.comfy.org/registry/publishing)
- [ComfyUI Registry standards](https://docs.comfy.org/registry/standards)
- [GitHub workflow dispatch API](https://docs.github.com/en/rest/actions/workflows#create-a-workflow-dispatch-event)
