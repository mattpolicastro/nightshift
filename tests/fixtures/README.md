# Fixtures

`worker1.jsonl` and `worker3b.jsonl` are real `claude -p --output-format
stream-json` transcripts from two early sandbox runs, kept because
`task.ran_verification` is a claim about what a worker *actually did* and
asserting it against a hand-written transcript would be circular:

- **`worker1`** ran the full verify chain and shipped. `ran_verification` must
  return `True`.
- **`worker3b`** escalated on a bad premise without verifying. It must return
  `False`.

**They are redacted.** The runs were against a private repo, so file paths,
package and symbol names have been replaced with neutral equivalents, and
assistant prose and commit bodies replaced with placeholders. What is preserved
is everything the tests read and everything that made these worth vendoring:
the event structure, the `rate_limit_info` and `total_cost_usd` telemetry, the
`permission_denials`, and the exact sequence and shape of Bash commands.
