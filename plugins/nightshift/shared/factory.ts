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
  pause: z.object({
    since: z.number().finite().nullable().optional(),
    auto_resumes_remaining: z.number().int().nonnegative().nullable().optional(),
  }).nullable().optional(),
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

// Landing is private transport, deliberately separate from the ordinary act verbs.
const landingChangeSchema = changeSchema.max(255).refine(value => !/\s/.test(value));
const attemptIdSchema = z.string().length(32).regex(/^[a-f0-9]{32}$/);
const boundedText = (limit: number) => z.string().refine(value => Array.from(value).length <= limit);
const landingReasonSchema = boundedText(8192);
const landingDiagnosticsSchema = z.array(boundedText(512)).max(20);
export const factoryLandingOfferSchema = z.object({
  change: landingChangeSchema,
  pr: z.string().max(512)
    .regex(/^https:\/\/github\.com\/[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?\/[A-Za-z0-9_.-]+\/pull\/[1-9][0-9]*$/)
    .refine(value => !/\s/.test(value) && !['.', '..'].includes(value.split('/')[4])),
  pause_id: boundedText(512).refine(value => value.startsWith('human_checks:') && !/[\x00-\x1f\x7f]/.test(value)),
  head: z.string().length(40).regex(/^[a-fA-F0-9]{40}$/).transform(value => value.toLowerCase()),
  since: z.number().finite(),
});
export const factoryLandingAttemptSchema = factoryLandingOfferSchema.extend({
  attempt_id: attemptIdSchema,
  created_at: z.number().finite(), updated_at: z.number().finite(),
  phase: z.enum(['starting', 'validating', 'recording checks', 'finishing', 'checking CI', 'merging']),
  outcome: z.enum(['succeeded', 'failed', 'refused', 'stopped']).nullable(),
  reason: landingReasonSchema, exit_code: z.number().int().nullable(),
  diagnostics: landingDiagnosticsSchema, overdue: z.boolean(),
});
export const factoryLandingStatusSchema = z.object({
  version: z.literal(1), attempts: z.array(factoryLandingAttemptSchema).max(20),
  total: z.number().int().nonnegative(), diagnostics: landingDiagnosticsSchema,
  offer: factoryLandingOfferSchema.nullable().default(null),
  current: factoryLandingAttemptSchema.nullable().default(null), truncated: z.boolean(),
});
export const factoryLandingInputSchema = z.object({
  change: landingChangeSchema.optional(), refresh: z.boolean().optional(),
}).strict();
export const factoryLandingSnapshotSchema = factoryLandingStatusSchema.extend({
  stale: z.boolean(), fetchedAt: z.string(), error: z.string().nullable(),
});
export const factoryLandInputSchema = factoryLandingOfferSchema.extend({ checks_done: z.literal(true) }).strict();
export const factoryLandStopInputSchema = z.object({ attempt_id: attemptIdSchema }).strict();
export const factoryLandResultSchema = z.discriminatedUnion('accepted', [
  z.object({ version: z.literal(1), accepted: z.literal(true), attempt_id: attemptIdSchema,
    reason: landingReasonSchema.optional() }),
  z.object({ version: z.literal(1), accepted: z.literal(false), attempt_id: attemptIdSchema.nullable(),
    reason: landingReasonSchema }),
]);
export const factoryLandStopResultSchema = z.discriminatedUnion('accepted', [
  z.object({ version: z.literal(1), accepted: z.literal(true), attempt_id: attemptIdSchema,
    stop_requested: z.literal(true), reason: landingReasonSchema }),
  z.object({ version: z.literal(1), accepted: z.literal(false), attempt_id: attemptIdSchema,
    reason: landingReasonSchema }),
]);
export type FactoryLandingOffer = z.infer<typeof factoryLandingOfferSchema>;
export type FactoryLandingAttempt = z.infer<typeof factoryLandingAttemptSchema>;
export type FactoryLandingInput = z.infer<typeof factoryLandingInputSchema>;
export type FactoryLandingSnapshot = z.infer<typeof factoryLandingSnapshotSchema>;
export type FactoryLandInput = z.infer<typeof factoryLandInputSchema>;
export type FactoryLandResult = z.infer<typeof factoryLandResultSchema>;
export type FactoryLandStopInput = z.infer<typeof factoryLandStopInputSchema>;
export type FactoryLandStopResult = z.infer<typeof factoryLandStopResultSchema>;

export const factoryLanding = defineRpc({
  name: 'factory.landing', input: factoryLandingInputSchema, output: factoryLandingSnapshotSchema,
});
export const factoryLand = defineRpc({
  name: 'factory.land', input: factoryLandInputSchema, output: factoryLandResultSchema,
});
export const factoryLandStop = defineRpc({
  name: 'factory.land.stop', input: factoryLandStopInputSchema, output: factoryLandStopResultSchema,
});
