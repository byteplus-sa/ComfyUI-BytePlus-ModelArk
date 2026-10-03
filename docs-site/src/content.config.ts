import { defineCollection } from 'astro:content';
import { docsLoader } from '@astrojs/starlight/loaders';
import { docsSchema } from '@astrojs/starlight/schema';
import { z } from 'astro/zod';

export const collections = {
	docs: defineCollection({
		loader: docsLoader(),
		schema: docsSchema({
			extend: z.object({
				// Node pages: the node ID from nodes.json. Template pages: the file name in example_workflows/.
				node_id: z.string().optional(),
				template_file: z.string().optional(),
			}),
		}),
	}),
};
