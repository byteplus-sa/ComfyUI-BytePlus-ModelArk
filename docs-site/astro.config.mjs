// @ts-check
import { defineConfig } from 'astro/config';
import starlight from '@astrojs/starlight';
import starlightImageZoom from 'starlight-image-zoom';
import starlightLinksValidator from 'starlight-links-validator';
import { readFileSync } from 'node:fs';

const nodes = JSON.parse(readFileSync(new URL('./src/data/nodes.json', import.meta.url), 'utf-8'));
const templates = JSON.parse(readFileSync(new URL('./src/data/templates.json', import.meta.url), 'utf-8'));

/** Group a list of {group} entries into Starlight sidebar groups, keeping the data order. */
function grouped(items, folder, label) {
	const groups = [];
	for (const item of items) {
		let group = groups.find((g) => g.label === item.group);
		if (!group) {
			group = { label: item.group, collapsed: false, items: [] };
			groups.push(group);
		}
		group.items.push({ label: label(item), slug: `${folder}/${item.slug}` });
	}
	return groups;
}

// https://astro.build/config
export default defineConfig({
	// GitHub Pages project site. Change both when a custom domain is added.
	site: 'https://byteplus-sa.github.io',
	base: '/ComfyUI-BytePlus-ModelArk',
	integrations: [
		starlight({
			title: 'BytePlus ModelArk for ComfyUI',
			description:
				'Seedream, Seedance, Seed LLM, Seed Speech and AI MediaKit nodes for ComfyUI, using your own BytePlus API keys.',
			social: [
				{ icon: 'github', label: 'GitHub', href: 'https://github.com/byteplus-sa/ComfyUI-BytePlus-ModelArk' },
			],
			editLink: {
				baseUrl: 'https://github.com/byteplus-sa/ComfyUI-BytePlus-ModelArk/edit/main/docs-site/',
			},
			plugins: [starlightImageZoom(), starlightLinksValidator()],
			customCss: ['./src/styles/custom.css'],
			sidebar: [
				{
					label: 'Get started',
					items: [
						{ slug: 'get-started/install' },
						{ slug: 'get-started/api-keys' },
						{ slug: 'get-started/first-run' },
					],
				},
				{
					label: 'Concepts',
					items: [
						{ slug: 'concepts/keys-and-regions' },
						{ slug: 'concepts/billing-and-cost' },
						{ slug: 'concepts/reference-media' },
						{ slug: 'concepts/draft-mode' },
						{ slug: 'concepts/interrupts' },
					],
				},
				{
					label: 'Nodes',
					collapsed: true,
					items: [{ slug: 'nodes' }, ...grouped(nodes, 'nodes', (n) => n.display_name.replace(/^BytePlus /, ''))],
				},
				{
					label: 'Templates',
					collapsed: true,
					items: [{ slug: 'templates' }, ...grouped(templates, 'templates', (t) => t.title)],
				},
				{ label: 'Models', slug: 'models' },
				{ label: 'Troubleshooting', slug: 'troubleshooting' },
				{ label: 'Contributing', slug: 'contributing' },
			],
		}),
	],
});
