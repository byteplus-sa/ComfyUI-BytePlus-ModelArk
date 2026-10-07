# Writing the docs

The site is Astro Starlight. Tables of inputs, outputs, models and the facts cards are **generated** from the code
(`scripts/generate_data.py` reads a running ComfyUI's `/object_info`, `example_workflows/` and `nodes/models_config.py`).
Pages only hold hand-written text around those components. Never copy an input table into a page by hand.

```bash
cd docs-site
npm install
python3 scripts/generate_data.py --object-info http://127.0.0.1:8188/object_info   # ComfyUI with the pack loaded
node scripts/check-docs.mjs      # format check, run it before every commit
npm run dev                      # http://localhost:4321/ComfyUI-BytePlus-ModelArk/
npm run build                    # also validates every link
```

## Rules for all text

- Frontmatter `title` and `description` are always in double quotes.
- English, plain words, short sentences. Write for someone who has used ComfyUI but never these nodes.
- Address the reader as “you”. Use present tense and active voice.
- Name UI elements as they appear: **Settings → BytePlus**, **Save Image**, `size_preset`. Input and output names in `code`.
- Link with **relative** paths so they work under the site's base path: from a node page `../seedream/`, from a template page
  `../../nodes/seedream/`, to a guide `../../get-started/api-keys/`. Always end with `/`.
- Never write a key, token, signed URL (anything with `?X-Tos…`, `Signature=`) or a real user's email. Say “your key”.
- Only state facts you checked in the code (`nodes/*.py`, `nodes/constants.py`, `nodes/models_config.py`), the README, or
  the live test data in `src/data/evidence.json`. If you are not sure, leave it out. Do not invent limits, prices or speeds.
- No Chinese text and no Jimeng/Doubao names. No em dashes; use a comma, a colon or a new sentence.
- Keep internal validation details out of public pages and captions: no test dates, pass/fail status, measured run times, execution-history reports or test-account anecdotes. Describe example outputs and useful customer guidance instead.
- Do not repeat what a generated table already says. Notes explain what the table cannot: how inputs work together, what to pick.

## Node page: `src/content/docs/nodes/<slug>.mdx`

`<slug>` and `node_id` come from `src/data/nodes.json`. The title is the node's display name without the “BytePlus ” prefix (or the full display name when the short one would be unclear, as for “BytePlus LLM”).

```mdx
---
title: Seedream 4.5 & 5.0
description: "<one sentence, 40 to 160 characters, starts with a verb>"
node_id: BytePlusSeedream
---

import NodeFacts from '../../../components/NodeFacts.astro';
import NodeInputs from '../../../components/NodeInputs.astro';
import NodeOutputs from '../../../components/NodeOutputs.astro';
import UsedIn from '../../../components/UsedIn.astro';
import Shot from '../../../components/Shot.astro';

<NodeFacts id="BytePlusSeedream" />

## What it does          2 to 4 sentences. Say whether it bills and what it needs connected.
## On the canvas         <Shot src="nodes/<slug>.png" alt="..." caption="..." />
## Inputs               <NodeInputs id="..." />, then optional "### Notes on inputs" (3 to 6 bullets)
## Outputs              <NodeOutputs id="..." />, then optional "### Notes on outputs"
## Limits and tips      3 to 8 bullets: media limits, models and regions, time, what happens on interrupt
## Common errors        a table `| Message | What to do |` with 2 to 5 rows, messages from constants.MESSAGES
## Used in templates    <UsedIn id="..." />
```

The H2 headings, their names and their order are fixed. `scripts/check-docs.mjs` enforces it.
Reference page to copy: `src/content/docs/nodes/seedream.mdx`.

Extra screenshots (for example one per model option) go in the same `## On the canvas` section as more `<Shot>` lines,
named `nodes/<slug>-<variant>.png`.

## Template page: `src/content/docs/templates/<slug>.mdx`

`<slug>` and `template_file` come from `src/data/templates.json`. The title is the template name.

```mdx
---
title: Seedream
description: "<one sentence, 40 to 160 characters>"
template_file: Seedream.json
---

import TemplateFacts from '../../../components/TemplateFacts.astro';
import Shot from '../../../components/Shot.astro';

<TemplateFacts file="Seedream.json" />

## What it makes              1 short paragraph: the result and the idea.
## The workflow               <Shot src="templates/<slug>-workflow.webp" .../> then a numbered list, one item per step, each linking its node page
## Run it                     numbered steps
## Settings worth changing    3 to 6 bullets
## Example result             <Shot src="templates/<slug>-result.webp" .../> and the sample media (see below)
## Time and cost              what affects runtime, what bills, what is cached
## Related                    links to the nodes and one next-step template
```

Reference page to copy: `src/content/docs/templates/seedream.mdx`.

Sample media for a template is listed in `src/data/evidence.json` (`media`) and sits in `public/media/`. Show it under
**Example result** with the `Media` component:

```mdx
import Media from '../../../components/Media.astro';

<Media kind="video" file="seedance-2.mp4" caption="..." />
<Media kind="audio" file="seed-audio-0.mp3" caption="..." />
```

## Images

- `src/assets/nodes/<slug>.png`: one canvas screenshot per node (Classic Canvas, default values, no keys visible).
- `src/assets/templates/<slug>-workflow.webp` and `<slug>-result.webp`: from the live test run (`scripts/import_evidence.py`).
- Alt text says what the picture shows, not “screenshot”. Captions are one sentence.
- `<Shot>` images open in a full-screen viewer when clicked or tapped (`public/lightbox.js`): actual size with panning on phones, fitted to the window on wide screens, with zoom buttons. Always use `<Shot>` for screenshots so they get it.
