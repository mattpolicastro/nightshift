## Context

The root README already includes PR #61's consumed-feedback behavior and
PR #28's job controls. The guide and limitations list still describe older
behavior. Updating by adding a changelog would leave those contradictions in
place; the affected explanations need replacement in their existing sections.

The implementation baseline is `a684e40`, which merges PR #61 for issue #6.
Local first-parent history establishes the PR mappings below. GitHub API
requests failed during drafting, so current issue states and the full #17
ledger have not been independently checked. The five required remaining gaps
come from the supplied issue description. Live verification remains a human
pre-merge check, not a claim that this draft already performed it.

Read-only sources are the three target docs and these existing capabilities:
`publish-base-sync`, `in-flight-file-map`, `spec-critic`, `issue-intake`,
`pr-revision`, `specloop-interface`, `attention`, `parallel-evidence-mutants`,
`mutation-selection`, `background-job-control`, `paused-job-delivery` and
`touchpoint-report`. All their requirements remain unchanged. In particular,
the spec-critic requirement names the original five items; the current prompt
adds three after PR #62 without removing those five. Document all eight; do
not use this documentation change to rewrite the canonical spec.

## Goals / Non-Goals

Give a new operator a consistent account of drafting, approval, building,
publication, evidence and intervention. Make every new historical claim
traceable to a merged PR, and distinguish closed fixes from remaining gaps.

Do not change runtime behavior, loosen assertions, introduce documentation
test infrastructure, or repair any remaining gap. Do not change the worker
engine's timestamp-based feedback description. Do not expand the root README
outside its specloop/front-door material.

## Decisions

### Documentation ownership and citations

Use `docs/specloop/README.md` for detailed behavior, `KNOWN-LIMITATIONS.md` for
current gaps, and `README.md` for the front-door overview and links. Replace
stale sentences in place; keep working enrollment and pause instructions.
Use explicit Markdown links labelled `PR #N` or `issue #N` with repository
URLs under `https://github.com/mattpolicastro/nightshift/pull/N` or
`https://github.com/mattpolicastro/nightshift/issues/N`. A closing issue and its
implementation PR are different references. Place citations beside the
behavior they support, not in an unexplained list of numbers.

| Topic | Issue, when applicable | Merged PRs in local history | Read-only implementation sources |
| --- | --- | --- | --- |
| Publication base integration | #46 | #59 | `specloop/loop.py`, `specloop/cli.py`, `nightshift/specloop_intake.py` |
| In-flight map and overlap feedback | #58 | #36, #37, #43, #60 | `specloop/propose.py` |
| Human tasks and spec guarantees | — | #30, #36 | `specloop/propose.py`, `specloop/feasibility.py` |
| Spec critic and expanded checklist | #51 | #57, #62 | `specloop/critic.py`, `specloop/propose.py` |
| Dependency gate | #24 | #29 | `nightshift/specloop_intake.py`, `nightshift/queue.py` |
| Label authorization | #5 | #42 | `nightshift/specloop_intake.py`, `nightshift/queue.py` |
| Revision feedback IDs | #6 | #61 | `nightshift/specloop_intake.py`, `nightshift/queue.py` |
| Idea file | #12 | #35 | `nightshift/specloop_intake.py` |
| Status isolation | #10 | #32 | `specloop/cli.py`, `specloop/state.py` |
| Parallel mutants | #4 | #27 | `specloop/evidence.py`, `specloop/manifest.py` |
| Timeout evidence | #31 | #44 | `specloop/evidence.py`, `specloop/loop.py` |
| Prose exclusion and measured evidence | #21 | #48, #63 | `specloop/evidence.py`, `specloop/manifest.py` |
| Guard-clause operators | #33 | #64 | `specloop/evidence.py` |
| Job inventory and stop | #8 | #28 | `nightshift/cli.py`, `nightshift/specloop_job.py` |
| Touchpoint report | #50 | #53 | `nightshift/touchpoints.py` |
| Parallel tests and scenario plugin | — | #54 | `pyproject.toml`, `examples/specloop/scenario_plugin.py` |
| Resolved attention and CLI gaps | #11, #14 | #22, #45 | `nightshift/specloop_attention.py`, `specloop/cli.py`, `specloop/prompts.py` |

This is a source map for the implementing agent, not a required table in the
operator guide. Do not label #36, #37, #54 or #62 as feature issues. Issue #58's
implementation is PR #60; issue #51's initial critic is PR #57.

### Publication and spec-stage descriptions

Describe driver `finals` / `prepare_publish` fetching the configured `pr_base`
from the source origin, creating a two-parent merge when needed, and running
trusted prepare/verify before readiness. Publish refetches once with a
20-second limit. If integration or verification is due, it saves running
state with no pause and returns a retryable error; `specloop serve` drives
preparation, then publication can retry at `ready_to_publish`. Publish does
not merge or verify inline. Describe `base_conflict` with unchanged head and
conflicting paths, `base_drift` with the retained merged head and check logs,
and driver `base_fetch` separately from a retryable publish fetch failure.
Repair and resume must pass verification before publication. Preserve same-PR
delivery and the human-checks boundary.

Explain that the in-flight map reads task-named paths from other running or
paused runs for the same source repository, excluding the current change and
isolating unreadable runs. It is a snapshot, not a reservation. Include
`file.py:function`, `file.py::function` and existing source-root filenames.
The first drafting round feeds back shared test-file edits; explicit hands-off
clauses do not count as edits. Residual overlap is a risk, not an unconditional
ban. Necessary avoidance instructions must name files in the draft because
implementers do not receive the map.

Replace the guide's three-round claim with the current default of four
drafting rounds. Human-task lint catches common human-only task forms;
feasibility also catches named-person checks and impossible tasks. Human
checks belong in `design.md`. Feasibility rejects breaking an existing spec
guarantee without a justified MODIFIED or REMOVED delta.

The critic follows passing lint and feasibility for proposals and spec
revisions. It receives both the full in-flight map and open-PR changed files.
It is a separate read-only review, allows at most one correction within the
drafting budget, and reports remaining blockers as contradictions and notes as
`Critic: ` risks. A failed review is not a pass. Enumerate its eight items:

1. Over-engineering.
2. File collisions and hidden instructions.
3. Invalidated protected assertions.
4. Conflicts with unowned behavior.
5. Tests broken by pending changes.
6. Unplanned migrations.
7. Retry and idempotency gaps.
8. Stale operator docs and manual steps.

### Front-door and evidence descriptions

Document dependency gating of new draft rounds only, latest-label-actor
authorization, durable UTF-8 idea files, and per-run JSON status errors. Keep
feedback trust separate from trigger authorization. Preserve the root README's
consumed-ID explanation, including comments arriving during a revision,
same-ID edits, immutable round snapshots and one-time legacy initialization.
Bring the guide into agreement without changing the worker instructions.

Document `[gates] mutation_parallel = 2` as the default and `1` as serial,
with an unmutated baseline before isolated concurrent mutant workspaces.
Only killed/survived results enter the score. Selected-test timeouts are
`timeout`; preparation/configure/build failures remain `invalid`. Generated
mutants without any judged killed/survived result are unmeasured and pause at
`gate_evidence`, even if all are equivalent. No eligible candidates is a
separate skip, still subject to scenario coverage.

Explain Python docstring/comment exclusion, the limited non-Python comment
filter, and case-sensitive literal whole-line substring `mutation_skip`
patterns, applied before sampling. Do not imply arbitrary strings or JSX
styles are automatically excluded. Give small Python examples for
`negate-condition` on bare-name `if`/`while` tests and `drop-return` replacing
a qualifying guard return with `pass`; avoid implying all returns qualify.

### Operations and limitation cleanup

Link the guide's job-control summary to the existing root README recovery
instructions. Keep pause, job stop and the independent supervisor distinct.
Document `uv run python -m nightshift.touchpoints`, `--since YYYY-MM-DD` and
`--json`: seven counters, observed local history, approximate draft dates,
unknown merge boundary and diagnostics. Counts can undercount missing or
overwritten history; their sum is category observations, not unique actions.

Show `uv run pytest -n 4` as available parallel execution, alongside the
required `uv run pytest` acceptance command. Explain that the scenario plugin
forwards passed IDs through report properties to the controller, which emits
deduplicated `SCENARIO` lines. Do not claim CI defaults to four workers:
`.github/workflows/ci.yml` currently invokes `uv run pytest -q` and ignores
Markdown-only changes.

Remove demonstrated fixed limitations: serial mutants, unchecked label
actors, branch-time feedback loss/replay, no job stop command, whole-status
failure, missing requested/exhausted-infra attention, idea text in argv, and
the CLI/prepare-prompt items resolved by PR #45. Split mixed bullets: job
inventory fixes visibility, not deadlines, retention or automatic retry.
Do not erase the separate crash-after-commit revision concern merely because
feedback consumption is durable: `specloop/revise.py` still has commit and
result persistence boundaries. Retain unresolved config, credential, quota,
platform and other gaps unless a specific merged fix establishes otherwise.

Add or refine the five #17 ledger gaps: an admission completing while paused
can build under the independent supervisor; stopped jobs stay held across
resume/restart/relabel and have no retry command (issue #34); touchpoints
undercount incomplete local history; JSX style filtering is descoped, with
explicit `mutation_skip` available; the automated front door and publication
use GitHub. GitHub-only does not mean local drafting or verification needs a
GitHub host. Mark these as remaining limitations, not promised work.

### Verification and human checklist

Documentation scenarios are checked against text, source, existing specs and
local merge history. Existing tests protect the runtime contracts; do not add
prose-matching tests or alter tests to make this documentation change pass.
Run `uv run pytest` on the completed implementation and record the result in
the PR. Check the implementation diff contains only the three allowed docs
and preserves all existing assertions. The draft itself is validated for
OpenSpec structure and complete scenario-to-task coverage separately.

Human checks for the implementation PR, never agent tasks:

- Matt reads the guide once as a new reader, following the front door through
  publication and a pause, and checks that the wording is short and concrete.
- On GitHub, confirm every cited PR exists and is merged, check the states of
  cited issues, confirm issue #6 is resolved, and compare remaining limitations
  with the current #17 ledger, including #34. Record discrepancies for a docs
  correction before merge. Local merge history cannot establish current issue
  state. No live workflow, service restart or actual job stop is needed.

## Risks / Trade-offs

- Concurrent merges can stale these docs again. Reconcile against the landed
  implementation at review; avoid promises about pending work.
- A long changelog would obscure the workflow. Use adjacent citations and
  short examples, keeping recovery detail in its existing location.
- Overlap snapshots, mutation skips and touchpoint counts have bounded
  meanings. State those bounds rather than implying guarantees they lack.
- A green suite protects existing behavior, not readability or citation
  status. Markdown-only CI may not run; the explicit local suite result and
  human checklist remain required.
