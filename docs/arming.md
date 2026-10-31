# Arming an issue

Arming checks issue wording and concurrent file work before applying `spec:draft`.
Run it from the current repository checkout with Git, `uv`, the project's Python
3.12+ environment. For GitHub, install and authenticate GitHub CLI (`gh`) for the
repository's host, with issue/PR access and permission to apply labels. Configured
Gitea uses its bot credentials and does not require `gh`.
The repository must already have a `spec:draft` label; the command never creates it.
Local run coverage requires access to Nightshift's existing run state.

## Invocation

```sh
uv run python -m nightshift.arm 76 77
uv run python -m nightshift.arm --check 76 77
uv run python -m nightshift.arm --force 76 77
uv run python -m nightshift.arm --check --force 76 77
```

Use positive issue numbers, optionally quoted as `'#76'` to protect the `#` from
shell comment parsing. Duplicates are processed once in argument order. Without
`--config`, no config or credential file is read: Git and
`gh repo view` resolve the current checkout and repository, including enterprise
GitHub hosts. Cross-repository issue arguments are unsupported. This is a module
command; integration into the single CLI is separate work.

## Configured Gitea

Use [forge configuration](../config.example.toml) to enroll the checkout with
`repos.forge` and an absolute `repos.dir`. `--config PATH` expands `~` and resolves
relative paths from the invocation directory. Exactly one enrollment's resolved
clone directory must match the Git checkout root; another worktree needs its own
matching configuration. Missing, invalid, unmatched or ambiguous config refuses
even with force. Named GitHub still uses ambient `gh` host/authentication and
requires discovery to match the configured owner/repository, ignoring case.

Provision the Gitea forge's `token_env` bot token in `~/.config/nightshift/env` or
the process environment. The file wins, including an empty value, which refuses
arming. File values are not exported. Landing tokens, manifest env and GitHub
credentials are not fallbacks. The repository's `spec:draft` label must exist.

```sh
uv run python -m nightshift.arm --config /absolute/path/to/config.toml --check 76
uv run python -m nightshift.arm --config /absolute/path/to/config.toml 76
```

Gitea armed-issue collection reads all pages and filters labels client-side using
current repository label IDs. Missing or duplicate label definitions and partial
context become findings. Every label POST is read back before success; unrelated
labels remain intact. A silent write or unavailable final confirmation fails,
including with force. An uncertain response gets one fresh logical label read,
never a replacement POST; that read may paginate and use existing GET retries.

Configured arming ([issue #131](https://github.com/mattpolicastro/nightshift/issues/131))
uses verified bot-backed labels. Gitea daemon drafting, admission, revisions,
publication and pending delivery now follow normal eligibility and foreign-journal
gates ([issue #150](https://github.com/mattpolicastro/nightshift/issues/150)). Force
or preflight cannot bypass actor authorization, health, pause or stopped-job holds.
Trigger actors must be exact case-insensitive allowlist members other than the bot;
arming with the bot token does not authorize a daemon round. A trusted human must
remove and reapply the trigger. [Terminal Gitea landing](landing.md) uses the human
token; Factory Land remains GitHub-only. See [remaining limitations](../KNOWN-LIMITATIONS.md#open-gaps).

## Issue template

Copy the contents below into the issue body and adapt the paths, behavior, test,
and human checks. Titles and comments do not satisfy lint rules.

```markdown
**Scope:** `nightshift/arm.py`, `tests/test_arm.py`

**What to do:** Check issue wording and file overlap before applying spec:draft.
Existing assertions are preserved.

**Definition of done:** `test_arm_check_mode_never_labels` verifies that clean and
failing checks, including --check --force, never write labels. Run `uv run pytest`.

**What CI cannot prove:** On the next few armed issues, compare findings with
subsequent drafter and critic objections; record missed objections and false positives.
```

All four bold markers are required at the start of a line, after optional
whitespace, with case-insensitive names. Inline text counts. A section ends at
the next bold section marker or Markdown heading. Markers inside fenced examples
do not create sections. A missing Definition of done gets a missing-section
finding; a present but empty one gets a named-test finding.

## Named tests

Put at least one named test in Definition of done. These forms satisfy the rule:

```text
`test_arm_check_mode_never_labels`
`tests/test_arm.py::test_arm_check_mode_never_labels`
'test_arm_check_mode_never_labels'
"test_arm_check_mode_never_labels"
test "check mode never labels"
test 'check mode never labels'
`waiting time turns to warning after three hours`
```

These forms do not satisfy it:

```text
uv run pytest
`uv run pytest`
`npm run test`
"the command behaves correctly"
test_arm_check_mode_never_labels
```

An empty section also fails. A test named only in another section does not count.
The rule checks syntax, not whether a test exists or proves the claimed behavior;
proposed tests are allowed. Backticked identifiers start with `test_`, followed
by letters, digits, or underscores; qualified pytest nodes may end in that name.
Backticked titles of at least three words also count, unless they start with
`uv `, `npm `, `python`, `node `, or `git ` (case-insensitively).

## Wording replacements

| Discouraged wording | Replacement |
| --- | --- |
| No existing tests are modified/changed/edited | existing assertions are preserved |
| Do not (or don't) modify/change/edit existing tests | existing assertions are preserved |
| Existing tests must remain unchanged | existing assertions are preserved |
| Manually, by hand, or visually in Definition of done | Move the human check under **What CI cannot prove:** |

The first family also recognizes singular `test`, omitted `are`, and `is` or
`to be`. This is finite, case-insensitive lexical lint: each occurrence of the
test-edit prohibitions anywhere in the body is reported, including quotations
and explanatory examples. Paraphrases can be missed. Manual-check words are
checked only in Definition of done. Describe executable assertions there and
put human judgment in What CI cannot prove.

## Dependencies and overlap

For a direct dependency, put `blocked-by: #77` on its own line in issue #76.
Bullets, quotes, mixed case, and multiple numbers such as `blocked-by: #77 #78`
are supported. To sequence #76 after #77 through an unarmed issue #90, put this
in #76:

```markdown
blocked-by: #90
```

Then put this in #90:

```markdown
blocked-by: #77
```

The resulting #76 → #90 → #77 chain exempts overlapping paths when checking
either #76 against #77 or #77 against #76. Intermediate issues need not be armed.
Sharing a prerequisite does not exempt overlap. Cycles terminate during checking
but are neither repaired nor newly linted; they may leave drafting blocked forever.
A proven chain removes only overlap findings, leaving wording and completion
findings intact. Failed dependency reads cannot establish an exemption.

An open direct blocker is acceptable for arming; the existing queue defers drafting
until blockers are closed. A direct blocker closed as `COMPLETED` passes completion
lint; `NOT_PLANNED` produces a finding. An unknown closed reason or failed lookup
is incomplete context. Gitea may omit completion reasons; closed alone is never
treated as `COMPLETED`. This does not change the daemon's closed-state gate.

Overlap compares Scope paths with open `spec:draft` issues, other requested
targets (even unlabelled), running/paused runs in the same resolved checkout,
and open front-door PRs. Self evidence is excluded by source issue identity.
Reports combine issue/run/PR evidence for each path and other issue and suggest
`blocked-by: #N`. Choose a real dependency order or revise Scope; the command
does not edit issue bodies or insert dependencies. An unidentified run requires
identifying its source issue first; no blocker number is guessed.

## Modes and exits

All modes print every finding and each target's final disposition. Targets retain
argument order; findings sort by code, location, and related identity.

| Mode | Open readable issue, no findings | Open readable issue, any findings |
| --- | --- | --- |
| Default | Add `spec:draft`; `armed`; exit 0 | No write; `unarmed (findings)`; exit 1 |
| `--check` | No write; `check clean`; exit 0 | No write; `check failed`; exit 1 |
| `--force` | Add `spec:draft`; `forced arming`; exit 0 | Print lint, overlap, and incomplete-context findings, add label; `forced arming`; exit 0 |
| `--check --force` | Same as `--check` | Same as `--check` |

These exits assume one target and a successful write where required. An existing
label is a no-op reported as `already armed` in default mode when clean or with
force. Findings still fail default/check mode on an already-labelled issue.
Closed or unreadable targets fail with exit 1 in every mode, including force.
Invalid arguments exit 2 before collection. Checkout/repository resolution
failure exits 1. Check mode never mutates the forge; force does not override check.

All candidates and shared context are collected before any label writes. A batch
exits 0 only if every target passes the selected policy; otherwise it exits 1.
Failures do not stop later targets, and successful labels are not rolled back.
After a label-write error, one fresh label read confirms success if `spec:draft`
is present (`armed (label confirmed after write failure)`); otherwise the write
is reported `unconfirmed (label write failed)` and fails. There is no blind retry.
The command only adds `spec:draft`: it never removes/reapplies labels to change
their actor, edits bodies, posts comments, creates PRs, or writes local receipts/state.

## Coverage limits

- Paths come only from Scope using the existing lexical path helper. Prefer
  explicit backticked file paths. Text from “out of bounds” onward is ignored
  (case-insensitively), as are clauses such as “do not edit” or “leave alone”.
  This applies to both target and other armed issues. Existing root files such as
  `README.md` and new nested files such as `docs/new-guide.md` participate; new root
  filenames are missed. Function suffixes (`nightshift/arm.py::main` or `:main`) are removed.
  Absolute, home-relative, URL, and `openspec/` tokens are excluded by the helper.
- Matching uses exact normalized tokens, including production, documentation,
  and test files. Globs remain literal: `tests/*.py` does not intersect a concrete
  `tests/test_arm.py` token. Directories/globs are not expanded, undeclared edits
  cannot be discovered, and paths outside Scope do not contribute issue evidence.
- The run helper silently skips unreadable runs. Recognized run names fully match
  `<repo-basename>-issue-<positive-number>-<slug>` with a 1–40 character lowercase
  alphanumeric/hyphen slug. Titles need not still match. Unknown runs stay visible.
- PRs participate only for same-repository heads named `spec/<front-door-run-name>`.
  Ordinary/custom and fork PRs are excluded. PR numbers are not source issue
  numbers. File evidence includes old/new rename paths and retains PR links.
  Missing metadata and PR-helper limitations become incomplete-context findings.
- Paginated issue/PR reads and cached dependency lookups give a best-effort
  snapshot, not an atomic reservation. GitHub calls have 30-second timeouts;
  the PR-file helper retains its own overall budget. Gitea uses 30-second socket
  timeouts and a 120-second PR-context request-start cutoff, not an elapsed-response
  guarantee; slow bodies can overrun these bounds. Partial failures retain
  available evidence. Other operators and daemon transitions can race the check;
  there is no cross-process lock, scheduler, or enforcement in the daemon.

Force deliberately permits overlap and incomplete context. The authenticated
label actor must still pass the existing daemon authorization checks. Arming does
not bypass trust, dependency, admission, or review rules and does not promise
acceptance by the drafter or critic. Later checks remain necessary.

## Human follow-up for the implementation PR

- On a real configured Gitea checkout, run check mode, then arm a suitable issue.
  Inspect `spec:draft` and unchanged unrelated labels; record sanitized results
  and review the guidance against the actual setup. This live check is pending.
- After enrollment prerequisites and trusted human trigger application, observe
  issue drafting and its WIP spec PR. Verify an unlisted actor is refused. Follow
  the [enrollment checklist](../README.md#quickstart) for daemon token exclusion
  and matching supervisor configuration; verified labels do not prove live drafting.
- On the next few issues armed with this command, compare its findings with
  subsequent drafter/critic objections.
- Record missed objections and false positives for refining the heuristics.
- Keep this comparison as human follow-up, not an implementing-agent task.
  CI fixtures establish the deterministic contract; they cannot establish how
  well these heuristics predict future reviews.
