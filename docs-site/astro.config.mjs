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
			logo: {
				light: './src/assets/byteplus-mark-light.svg',
				dark: './src/assets/byteplus-mark-dark.svg',
				alt: 'BytePlus',
			},
			head: [
				// External links (GitHub, consoles) open in a new tab.
				{
					tag: 'script',
					content:
						"document.addEventListener('DOMContentLoaded',function(){for(const a of document.querySelectorAll('a[href^=\"http\"]')){try{if(new URL(a.href).host!==location.host){a.target='_blank';a.rel='noopener noreferrer';}}catch(e){}}});",
				},
			],
			description:
				'Seedream, Seedance, Seed, DeepSeek and GLM language models, Seed Speech and AI MediaKit nodes for ComfyUI, using your own BytePlus API keys.',
			social: [
				{ icon: 'github', label: 'GitHub', href: 'https://github.com/byteplus-sa/ComfyUI-BytePlus-ModelArk' },
			],
			editLink: {
				baseUrl: 'https://github.com/byteplus-sa/ComfyUI-BytePlus-ModelArk/edit/main/docs-site/',
			},
			plugins: [starlightImageZoom(), starlightLinksValidator({ errorOnRelativeLinks: false })],
			customCss: ['./src/styles/custom.css'],
			expressiveCode: {
				themes: ['vitesse-black', 'vitesse-light'],
				styleOverrides: { borderRadius: '8px', borderColor: 'var(--sl-color-hairline)', codeBackground: 'var(--sl-color-gray-6)' },
			},
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
					items: [{ slug: 'nodes', label: 'Overview' }, ...grouped(nodes, 'nodes', (n) => (n.display_name.replace(/^BytePlus /, '').length < 5 ? n.display_name : n.display_name.replace(/^BytePlus /, '')))],
				},
				{
					label: 'Templates',
					collapsed: true,
					items: [{ slug: 'templates', label: 'Overview' }, ...grouped(templates, 'templates', (t) => t.title)],
				},
				{ label: 'Models', slug: 'models' },
				{ label: 'Troubleshooting', slug: 'troubleshooting' },
				{ label: 'Contributing', slug: 'contributing' },
			],
		}),
	],
});
