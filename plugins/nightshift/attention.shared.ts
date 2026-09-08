import { defineRpc } from '@getpaseo/plugin/server';
import { z } from 'zod';

export const taskSchema = z.object({
  key: z.string(), repo: z.string(), issue: z.number().int().positive(),
  title: z.string(), summary: z.string(),
  state: z.enum(['awaiting_merge', 'needs_decision', 'resolved']),
  url: z.string().url(), checkedAt: z.string().nullable(), stale: z.boolean(),
});
export const snapshotSchema = z.object({
  tasks: z.array(taskSchema), stale: z.array(taskSchema),
  cached: z.boolean(), fetchedAt: z.string(), error: z.string().nullable(),
});
export type Snapshot = z.infer<typeof snapshotSchema>;
export type Task = z.infer<typeof taskSchema>;
export const attention = defineRpc({
  name: 'attention.read',
  input: z.object({ refresh: z.boolean() }),
  output: snapshotSchema,
});
