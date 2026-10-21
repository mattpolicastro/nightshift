import { defineRpc } from '@getpaseo/plugin';
import { z } from 'zod';

export const factoryVerbSchema = z.enum(['resume', 'accept', 'publish', 'finish', 'abort']);
const changeSchema = z.string().regex(/^[A-Za-z0-9][A-Za-z0-9._-]*$/);
export const factoryActionSchema = z.object({
  kind: z.string(), headline: z.string(), why: z.string(), command: z.string(),
  actions: z.array(factoryVerbSchema),
  pause_id: z.string().optional(),
});
export const factoryContextSchema = z.object({
  summary: z.string(), repo: z.string(), branch: z.string(),
  task: z.object({ label: z.string(), section: z.string(), text: z.string() }).nullable(),
  detail: z.string(),
  recent: z.array(z.object({ at: z.string(), text: z.string() })),
  github: z.string(),
});
export const factoryRunSchema = z.object({
  change: changeSchema, status: z.enum(['running', 'paused', 'done', 'aborted']),
  tasks: z.object({ done: z.number().int().nonnegative(), total: z.number().int().nonnegative() }),
  pr: z.string(), action: factoryActionSchema.optional(), context: factoryContextSchema.optional(),
});
export const factorySnapshotSchema = z.object({
  runs: z.array(factoryRunSchema), stale: z.boolean(), fetchedAt: z.string(),
  error: z.string().nullable(),
});
export const factoryActInputSchema = z.object({
  change: changeSchema, verb: factoryVerbSchema,
  note: z.string().refine(value => Array.from(value).length <= 2000).optional(),
  // The pause the page showed; specloop refuses if the run has moved to another.
  expect: z.string().regex(/^[a-z_]+:[A-Za-z0-9:._-]*$/).optional(),
});
export const factoryActResultSchema = z.object({
  version: z.literal(1), outcome: z.enum(['done', 'refused']),
  verb: factoryVerbSchema, detail: z.string(),
});
export type FactoryRun = z.infer<typeof factoryRunSchema>;
export type FactorySnapshot = z.infer<typeof factorySnapshotSchema>;
export type FactoryActInput = z.infer<typeof factoryActInputSchema>;
export type FactoryActResult = z.infer<typeof factoryActResultSchema>;

export const factoryRuns = defineRpc({
  name: 'factory.runs',
  input: z.object({ refresh: z.boolean().optional() }),
  output: factorySnapshotSchema,
});
export const factoryAct = defineRpc({
  name: 'factory.act', input: factoryActInputSchema, output: factoryActResultSchema,
});
