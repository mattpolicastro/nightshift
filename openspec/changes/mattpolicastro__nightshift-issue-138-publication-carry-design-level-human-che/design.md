## Context

The implementation follows #132. In this checkout, `specloop/loop.py:finals` renders sorted `Manifest.coverage_exempt` entries into `ready_to_publish` detail. `specloop/cli.py:_pr_body` independently renders those entries for new PRs. `nightshift/specloop_intake.py:publish_pending` records the ready detail in the publication receipt's `checks` before publishing, then uses that receipt to deliver or recover the comment. Updating the PR body alone misses adopted front-door PRs and their receipts.

The existing task/scenario human-check source here is `coverage_exempt`; there is no separate tasks.md human-check collector. Its sorted order and per-surface rendering stay intact. Implementation checkboxes in tasks.md must not become human checks. If the prerequisite introduces another existing human-check source, retain its order before design additions as well.

The relevant existing contracts are `issue-intake` publication and recoverable delivery, `publish-base-sync` readiness and receipt recovery, `specloop-interface` PR adoption and JSON, `run-actions` target/lock rules, and `merge-readiness` human-check source and conservative classification. This change adds collection without replacing any of those requirements. The clone has no AGENTS.md; README.md and the relevant code/specs were consulted.

## Goals / Non-Goals

Goals: carry pending design checks from the run's committed head into readiness, receipt, comment, and new-PR body; preserve existing checks and order; define a bounded physical-line rule; verify the path with local Git and injected service runners.

Non-goals: a Markdown AST, inference from prose or arbitrary headings, collecting ordinary implementation tasks, marking work complete, changing post-deploy annotations or readiness policy, rewriting adopted PR bodies, historical backfill, and live publication during implementation.

## Decisions

### One collector shared by both producers

Add `design_human_checks(ctx, head) -> list[str]` and a small append/deduplication helper in `specloop/loop.py`, next to readiness preparation. Read `openspec/changes/<ctx.m.change>/design.md` from `ctx.repo` at the explicit commit `head`, using the repository's Git helpers. Do not read the source working tree or a different branch. Establish absence with a successful Git tree lookup; absent design is an empty contribution, while Git/read/decode failures propagate and cannot silently establish readiness with an empty checklist.

Call the helper from `finals` after base preparation at its resulting head, and from `specloop/cli.py:_pr_body` at the published head. Keeping it in loop avoids a loop-to-CLI import cycle and avoids a new module for a small operation. This is the minimum necessary addition to the issue's stated file scope: readiness detail is produced in loop, not CLI. Keep all production changes for this concern together in task 1.1.

Existing scenario rows remain sorted by scenario ID and retain their current formatting: readiness uses a colon and the generated PR body uses an em dash. Append design rows to each existing list without reformatting earlier entries. Seed a seen set with existing rows, then append only unseen design rows in physical source order. Compare the complete checklist line after removing only outer spaces/tabs; do not normalize case, internal whitespace, punctuation, backticks, or scenario IDs. Preserve existing rows even if two existing sources already supplied duplicates. Design duplicates of earlier rows, including previous design rows, are omitted. Equivalent wording with different punctuation is not an exact duplicate.

### Deterministic physical-line rule

Use `splitlines()`; for recognition and emitted design lines, remove leading/trailing ASCII spaces and tabs only. On those normalized physical lines:

1. Open a selected section only on exact, case-sensitive `## Human checks` or `### Verification and human rollout checklist`; remember depth two or three respectively.
2. A line matching `^(#{1,6})[ \t]+` closes the selected section when its heading depth is equal or shallower. Process that same line as a possible selected heading, so repeated selected sections accumulate in document order. Deeper headings leave the selected section active. EOF closes it.
3. While active, collect lines starting with the literal `- [ ]` followed by a space or tab and nonblank item text. Keep each complete normalized line, including its checkbox marker. Checked `[x]`/`[X]` items, other bullet styles, prose, and continuation lines contribute nothing.
4. Fences, HTML, indentation, and block nesting have no semantic meaning. A matching line in a code example or HTML block counts exactly as a physical line outside it. A matching heading there also acts as a heading. Do not add heuristics that suppress an otherwise recognized unchecked item. Such an item stays pending and blocks readiness under existing outstanding/unknown-check rules unless its scenario already has the required post-deploy annotation.

Unsupported heading spellings, including closing hash decorations or different case, do not open a section. This deliberately supports only the two requested forms. No Markdown parser or fence/HTML/indentation state is needed. Repeated headings and deeper subsections have explicit behavior rather than an ambiguous fallback.

### Preserve receipt and consumer contracts

Leave `nightshift/specloop_intake.py` unchanged. Its existing snapshot of ready detail must contain all collected rows before publish replaces the pause with `human_checks`; its `(change, head, pr)` identity, round marker, retry handling, and single-comment recovery remain authoritative. Keep the existing detail header and `Next:` line, JSON fields, target validation, base-sync handoff, PR adoption, and hosted-CI reminder. A refreshed readiness pass after integration recomputes design rows from the integrated head.

Leave merge readiness unchanged. For adopted spec PRs lacking `## Checks a human must make`, it already falls back to receipt checks. An unannotated or unmapped design check must therefore block readiness under existing rules. The existing preference for an explicit PR-body section also remains unchanged; this change does not rewrite an adopted body's explicit checklist or claim to change that precedence.

### Tests and bounded implementation scope

Use new `tests/test_specloop_design_checks.py` for collector and rendering coverage and new `tests/test_specloop_design_publication.py` for receipt/delivery integration. Reuse or import existing local fixture helpers without editing their files. Test named outputs and persisted receipt contents, not only helper return values. No live forge, model provider, host application, or sandbox-exec dependency is needed for these new tests: use temporary committed Git repositories, stub unrelated final verification, and inject publication/GitHub responses at existing boundaries.

`test_task_checks_keep_order_before_design_checks` exercises multiple existing scenario checks inserted in unsorted manifest order, multiple design sections, a duplicate of an existing rendered row, repeated design rows, and near-duplicate text that must remain distinct. Parameterize both output surfaces to preserve their existing punctuation.

`test_design_human_checks_reach_publication` starts from a committed design with an unchecked unannotated check and no coverage exemptions, obtains readiness through real `finals` and status, then runs the existing publication delivery path. Assert the row in ready detail, persisted `checks`, and the single delivered comment; exercise lost-response/retry recovery and verify existing readiness blocks the adopted spec PR whose body lacks a human-check section. Do not hand-author the ready detail: that would miss the original defect.

Do not edit these in-flight paths: `README.md`, `docs/specloop/prepare.md`, `forge/gitea.py`, `forge/http.py`, `nightshift/forge/gitea.py`, `tests/contract/conftest.py`, `tests/contract/test_gitea_contract.py`, `tests/fake_gitea.py`, `tests/test_dependency_snapshots.py`, or `tests/test_forge_gitea.py`. Preserve every existing assertion. Acceptance is green `uv run pytest`; no workflow or fixture weakening is authorized.

## Risks / Trade-offs

- Literal examples inside selected sections may become extra human checks. This is intentional conservative behavior; semantic exception handling would create an unbounded parser task.
- Exact-text deduplication preserves near duplicates and the existing colon/em-dash difference between surfaces. It avoids conflating checks whose wording matters.
- Missing design remains compatible with old changes, but an unreadable committed design must fail collection rather than imply no checks.
- Existing delivered receipts and already-paused runs are not migrated. The guarantee applies when readiness is generated by the updated collector; normal driver recomputation handles subsequent readiness passes.
- CI proves local propagation and recovery, not a real publication. The following check stays pending until a person performs it.

## Human checks

- [ ] On the next real change whose design contains one of the supported checklist headings, compare its committed unchecked items with the ready-to-publish detail, saved publication receipt, and posted human-check comment. Confirm existing task checks appear first, exact duplicates appear once, and an outstanding unannotated design check prevents merge readiness when the spec PR body has no explicit human-check section. Record the PR and result; do not infer completion from CI.
