import { defineRpc } from '@getpaseo/plugin';
import { z } from 'zod';

export const taskSchema = z.object({
  key: z.string(), repo: z.string(), issue: z.number().int().positive(),
  title: z.string(), summary: z.string(),
  state: z.enum(['awaiting_merge', 'needs_decision', 'resolved']),
  url: z.string().url(), checkedAt: z.string().nullable(), stale: z.boolean(),
});
// The reader's cache envelope; worker `tasks`/`stale` stay for older clients and are always empty.
export const snapshotSchema = z.object({
  tasks: z.array(taskSchema), stale: z.array(taskSchema),
  cached: z.boolean(), fetchedAt: z.string(), error: z.string().nullable(),
});
export type Snapshot = z.infer<typeof snapshotSchema>;
export type Task = z.infer<typeof taskSchema>;

export const escalationSchema = z.object({
  key: z.string(), repo: z.string(), number: z.number().int().positive(),
  kind: z.enum(['issue', 'pr']), title: z.string(), url: z.string().url(),
  summary: z.string().nullable(), summaryCheckedAt: z.string().nullable(),
  summaryStale: z.boolean(), summaryError: z.string().nullable(),
});
export const repositorySchema = z.object({
  repo: z.string(), label: z.string(), entries: z.array(escalationSchema),
  checkedAt: z.string().nullable(), stale: z.boolean(), error: z.string().nullable(),
});
export const pauseSchema = z.object({
  change: z.string(), reason: z.string(), checkedAt: z.string().nullable(), stale: z.boolean(),
});
export const attentionSnapshotSchema = snapshotSchema.extend({
  repositories: z.array(repositorySchema), pauses: z.array(pauseSchema),
  // Set only when the host CLI predates front-door attention.
  frontDoorError: z.string().nullable(), pausesError: z.string().nullable(),
  // Online, error-free and every source checked: the only state that may claim an all-clear.
  complete: z.boolean(),
});
export type AttentionSnapshot = z.infer<typeof attentionSnapshotSchema>;
export type Escalation = z.infer<typeof escalationSchema>;
export type RepositoryAttention = z.infer<typeof repositorySchema>;
export type Pause = z.infer<typeof pauseSchema>;
export const attention = defineRpc({
  name: 'attention.read',
  input: z.object({ refresh: z.boolean() }),
  output: attentionSnapshotSchema,
});
