import nodes from '../data/nodes.json';
import templates from '../data/templates.json';

export type Node = (typeof nodes)[number];
export type Template = (typeof templates)[number];

export const base = import.meta.env.BASE_URL.replace(/\/$/, '');

export function getNode(id: string): Node {
	const node = nodes.find((n) => n.id === id);
	if (!node) throw new Error(`Unknown node id: ${id}`);
	return node;
}

export function getTemplate(file: string): Template {
	const template = templates.find((t) => t.file === file);
	if (!template) throw new Error(`Unknown template file: ${file}`);
	return template;
}

export function nodeUrl(id: string) {
	return `${base}/nodes/${getNode(id).slug}/`;
}

export function templateUrl(t: Template) {
	return `${base}/templates/${t.slug}/`;
}

/** Templates that contain a node. */
export function templatesUsing(id: string): Template[] {
	return templates.filter((t) => t.nodes.some((n) => n.id === id));
}

/** Short text for default / range, or '' when there is nothing to show. */
export function defaultText(input: any): string {
	if (input.default === undefined || input.default === '' || input.default === null) return '';
	if (typeof input.default === 'boolean') return input.default ? 'on' : 'off';
	return String(input.default);
}

export function rangeText(input: any): string {
	if (input.min === undefined && input.max === undefined) return '';
	if (input.type === 'group of inputs') return '';
	const lo = input.min ?? '';
	const hi = input.max ?? '';
	return `${lo}–${hi}`;
}
