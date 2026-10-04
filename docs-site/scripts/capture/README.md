# Taking the docs screenshots

Node pages show one canvas screenshot per node (`src/assets/nodes/<slug>.png`), template pages show the
workflow (`src/assets/templates/<slug>-workflow.webp`) and a result image from a live run. Retake them when
a node or template changes.

1. Start a ComfyUI that has this pack installed and **no keys saved** (screenshots need no API calls).
2. `python3 scripts/capture/capture_server.py` (port 8300).
3. Open ComfyUI in a browser, then in the page console load the helpers and call them:

   ```js
   const s = document.createElement('script'); s.src = 'http://127.0.0.1:8300/capture.js'; document.head.append(s);
   await DOCS.shotNode('BytePlusSeedream', 'nodes/seedream.png', { prompt: 'A glossy 3D badge of a paper crane' });
   await DOCS.shotWorkflow('seedream');
   ```

4. Convert the workflow PNGs: files land in `src/assets/templates/_wf/<slug>.png`; save them as
   `src/assets/templates/<slug>-workflow.webp` (max 2400 px wide, quality 86), then delete `_wf/`.
5. Result images and sample media of a live run: `scripts/import_evidence.py`, then `scripts/redact_results.py`
   (masks asset, group and task IDs). Check every new image for IDs, keys and signed URLs before committing.
