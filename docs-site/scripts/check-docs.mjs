#!/usr/bin/env node
// Format check for the node and template pages (see CONTRIBUTING-DOCS.md).
//   node scripts/check-docs.mjs            errors fail, placeholder images are warnings
//   node scripts/check-docs.mjs --strict   placeholder images fail too
import { readFileSync, readdirSync, existsSync, statSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const docs = join(root, 'src/content/docs');
const strict = process.argv.includes('--strict');
const nodes = JSON.parse(readFileSync(join(root, 'src/data/nodes.json'), 'utf-8'));
const templates = JSON.parse(readFileSync(join(root, 'src/data/templates.json'), 'utf-8'));
const evidence = JSON.parse(readFileSync(join(root, 'src/data/evidence.json'), 'utf-8'));

const errors = [];
const warnings = [];
const fail = (file, message) => errors.push(`${file}: ${message}`);
const warn = (file, message) => warnings.push(`${file}: ${message}`);

const NODE_H2 = ['What it does', 'On the canvas', 'Inputs', 'Outputs', 'Limits and tips', 'Common errors', 'Used in templates'];
const TEMPLATE_H2 = ['What it makes', 'The workflow', 'Run it', 'Settings worth changing', 'Example result', 'Time and cost', 'Related'];
const H3_ALLOWED = { Inputs: ['Notes on inputs'], Outputs: ['Notes on outputs'] };

function parse(path) {
	const text = readFileSync(path, 'utf-8');
	const match = text.match(/^---\n([\s\S]*?)\n---\n([\s\S]*)$/);
	if (!match) return null;
	const front = {};
	for (const line of match[1].split('\n')) {
		const m = line.match(/^([a-z_]+):\s*(.*)$/);
		if (m) front[m[1]] = m[2].replace(/^["']|["']$/g, '');
	}
	return { front, body: match[2] };
}

function headings(body) {
	// ignore fenced code
	const clean = body.replace(/```[\s\S]*?```/g, '');
	return [...clean.matchAll(/^(#{2,3}) (.+)$/gm)].map((m) => ({ level: m[1].length, text: m[2].trim() }));
}

function sectionBody(body, title) {
	const clean = body.replace(/```[\s\S]*?```/g, (m) => m);
	const re = new RegExp(`^## ${title.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\n([\\s\\S]*?)(?=^## |$(?![\\s\\S]))`, 'm');
	const m = clean.match(re);
	return m ? m[1] : '';
}

const BANNED = [
	[/—/, 'em dash'],
	[/[一-鿿]/, 'Chinese text'],
	[/jimeng|doubao/i, 'Jimeng/Doubao name'],
	[/X-Tos-|Signature=|X-Amz-/i, 'signed URL'],
	[/\b(sk-[A-Za-z0-9]{16,}|AKLT[A-Za-z0-9]{10,})/, 'something that looks like a key'],
	[/\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b/, 'a UUID (could be a key)'],
	[/TODO|FIXME|lorem ipsum/i, 'placeholder text'],
	[/\]\(\/(?!\/)/, 'root-relative link (use ../ paths so the base path works)'],
];

function imageSize(path) {
	// PNG / WebP / JPEG width without extra dependencies
	const b = readFileSync(path);
	if (b.slice(0, 8).toString('hex') === '89504e470d0a1a0a') return b.readUInt32BE(16);
	if (b.slice(0, 4).toString() === 'RIFF' && b.slice(8, 12).toString() === 'WEBP') {
		const kind = b.slice(12, 16).toString();
		if (kind === 'VP8X') return 1 + b.readUIntLE(24, 3);
		if (kind === 'VP8 ') return b.readUInt16LE(26) & 0x3fff;
		if (kind === 'VP8L') return 1 + (b.readUInt16LE(21) & 0x3fff);
	}
	let i = 2;
	while (i < b.length) {
		if (b[i] !== 0xff) break;
		const marker = b[i + 1];
		const len = b.readUInt16BE(i + 2);
		if (marker >= 0xc0 && marker <= 0xc3) return b.readUInt16BE(i + 7);
		i += 2 + len;
	}
	return 0;
}

function checkCommon(file, page, kind, expectedTitle, expectedKey, keyName, h2) {
	const { front, body } = page;
	if (front.title !== expectedTitle) fail(file, `title must be "${expectedTitle}", found "${front.title}"`);
	const description = front.description ?? '';
	if (description.length < 40 || description.length > 160) fail(file, `description must be 40 to 160 characters (is ${description.length})`);
	if (front[keyName] !== expectedKey) fail(file, `${keyName} must be "${expectedKey}", found "${front[keyName]}"`);

	const found = headings(body);
	const second = found.filter((h) => h.level === 2).map((h) => h.text);
	if (JSON.stringify(second) !== JSON.stringify(h2)) {
		fail(file, `H2 headings must be exactly: ${h2.join(' | ')}\n      found: ${second.join(' | ')}`);
	}
	let current = null;
	for (const h of found) {
		if (h.level === 2) current = h.text;
		else if (!(H3_ALLOWED[current] ?? []).includes(h.text)) fail(file, `H3 "${h.text}" is not allowed under "${current}"`);
	}
	for (const [re, what] of BANNED) if (re.test(body)) fail(file, `contains ${what}`);
	if (/<Shot [^>]*alt=["'](screenshot|image|picture)["']/i.test(body)) fail(file, 'alt text must describe the picture');
	for (const m of body.matchAll(/<Shot [^>]*src="([^"]+)"/g)) {
		const path = join(root, 'src/assets', m[1]);
		if (!existsSync(path)) fail(file, `image missing: src/assets/${m[1]}`);
		else if (imageSize(path) < 400) (strict ? fail : warn)(file, `image is still a placeholder: ${m[1]}`);
	}
	for (const m of body.matchAll(/<Media [^>]*file="([^"]+)"/g)) {
		if (!existsSync(join(root, 'public/media', m[1]))) fail(file, `media missing: public/media/${m[1]}`);
	}
	const words = body.replace(/<[^>]+>/g, ' ').split(/\s+/).filter(Boolean).length;
	if (words < 120) fail(file, `too short (${words} words)`);
	return { body };
}

// nodes
const nodeDir = join(docs, 'nodes');
const expectedNodeFiles = new Set(nodes.map((n) => `${n.slug}.mdx`));
for (const f of readdirSync(nodeDir)) {
	if (f === 'index.mdx') continue;
	if (!expectedNodeFiles.has(f)) fail(`nodes/${f}`, 'no node with this slug in nodes.json');
}
for (const n of nodes) {
	const file = `nodes/${n.slug}.mdx`;
	const path = join(docs, file);
	if (!existsSync(path)) { fail(file, 'page is missing'); continue; }
	const page = parse(path);
	if (!page) { fail(file, 'no frontmatter'); continue; }
	const { body } = checkCommon(file, page, 'node', n.display_name.replace(/^BytePlus /, ''), n.id, 'node_id', NODE_H2);
	for (const tag of ['NodeFacts', 'NodeInputs', 'NodeOutputs', 'UsedIn']) {
		if (!new RegExp(`<${tag} id="${n.id}" ?/>`).test(body)) fail(file, `needs <${tag} id="${n.id}" />`);
	}
	if (!body.includes(`src="nodes/${n.slug}.png"`)) fail(file, `needs <Shot src="nodes/${n.slug}.png" ... />`);
	const errorsSection = sectionBody(body, 'Common errors');
	const rows = errorsSection.split('\n').filter((l) => l.startsWith('|')).length - 2;
	if (rows < 2 || rows > 5) fail(file, `Common errors needs 2 to 5 table rows (has ${rows})`);
	if (!/\| Message \| What to do \|/.test(errorsSection)) fail(file, 'Common errors table header must be "| Message | What to do |"');
	const bullets = (sectionBody(body, 'Limits and tips').match(/^- /gm) ?? []).length;
	if (bullets < 3 || bullets > 8) fail(file, `Limits and tips needs 3 to 8 bullets (has ${bullets})`);
	const what = sectionBody(body, 'What it does').replace(/\s+/g, ' ').trim();
	const sentences = what.split(/(?<=[.!?])\s+/).filter(Boolean).length;
	if (sentences < 2 || sentences > 6) fail(file, `What it does needs 2 to 4 sentences (has ${sentences})`);
}

// templates
const tplDir = join(docs, 'templates');
const expectedTplFiles = new Set(templates.map((t) => `${t.slug}.mdx`));
for (const f of readdirSync(tplDir)) {
	if (f === 'index.mdx') continue;
	if (!expectedTplFiles.has(f)) fail(`templates/${f}`, 'no template with this slug in templates.json');
}
for (const t of templates) {
	const file = `templates/${t.slug}.mdx`;
	const path = join(docs, file);
	if (!existsSync(path)) { fail(file, 'page is missing'); continue; }
	const page = parse(path);
	if (!page) { fail(file, 'no frontmatter'); continue; }
	const { body } = checkCommon(file, page, 'template', t.title, t.file, 'template_file', TEMPLATE_H2);
	if (!new RegExp(`<TemplateFacts file="${t.file.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}" ?/>`).test(body)) fail(file, `needs <TemplateFacts file="${t.file}" />`);
	const run = evidence[t.slug];
	if (!body.includes(`src="templates/${t.slug}-workflow.webp"`)) fail(file, `needs <Shot src="templates/${t.slug}-workflow.webp" ... />`);
	if (run?.status === 'success') {
		if (!body.includes(`src="templates/${t.slug}-result.webp"`)) fail(file, `needs <Shot src="templates/${t.slug}-result.webp" ... />`);
		for (const m of run.media) {
			const name = m.path.replace(/^media\//, '');
			if (!body.includes(`file="${name}"`)) fail(file, `Example result must show the sample media <Media kind="${m.type}" file="${name}" />`);
		}
	} else if (!/not (been )?(run|completed|tested)/i.test(sectionBody(body, 'Example result'))) {
		fail(file, 'this template has no successful live run: Example result must say it was not run end to end');
	}
	const steps = (sectionBody(body, 'The workflow').match(/^\d+\. /gm) ?? []).length;
	if (steps < 2) fail(file, 'The workflow needs a numbered list with at least 2 steps');
	const run_steps = (sectionBody(body, 'Run it').match(/^\d+\. /gm) ?? []).length;
	if (run_steps < 2) fail(file, 'Run it needs a numbered list with at least 2 steps');
	const bullets = (sectionBody(body, 'Settings worth changing').match(/^- /gm) ?? []).length;
	if (bullets < 3 || bullets > 6) fail(file, `Settings worth changing needs 3 to 6 bullets (has ${bullets})`);
	for (const n of t.nodes) {
		const slug = nodes.find((x) => x.id === n.id).slug;
		if (!body.includes(`nodes/${slug}/`)) fail(file, `must link the node page nodes/${slug}/ (the template uses ${n.id})`);
	}
	if (!/\.\.\/\.\.\/get-started\/api-keys\/|api-keys/.test(sectionBody(body, 'Run it'))) warn(file, 'Run it should link to the API keys page');
}

for (const w of warnings) console.warn(`warning  ${w}`);
for (const e of errors) console.error(`error    ${e}`);
console.log(`${nodes.length} node pages, ${templates.length} template pages checked: ${errors.length} errors, ${warnings.length} warnings`);
process.exit(errors.length ? 1 : 0);
