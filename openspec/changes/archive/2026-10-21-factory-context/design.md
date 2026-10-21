## Context

`specloop status --json` builds one entry per run in `specloop/cli.py` (the
`action` object for paused runs is added there). The plugin's server
(`plugins/nightshift/server/factory.ts`) validates that JSON with zod, projects
display fields and replaces the home directory with `~`; the client renders rows
in `client/factory.tsx`, with testable logic in `client/factory-model.ts`.

## Decisions

### `context` in status JSON

Every run entry gains:

```
"context": {
  "summary": "<first paragraph of the ## Why section of proposal.md, at most 400 characters>",
  "repo": "<manifest run.repo, absolute>",
  "branch": "<run branch>",
  "task": {"label": "2.3", "section": "Paseo plugin", "text": "<task text, at most 300 characters>"} | null,
  "detail": "<the full pause detail, at most 1,500 characters; empty when not paused>",
  "recent": [{"at": "<ISO 8601 UTC>", "text": "<plain words>"}, ...],   // newest first, at most 5
  "github": "https://github.com/<owner>/<repo>/tree/<branch>/openspec/changes/<change>" | ""
}
```

- `summary` and `task` are read from the run's branch head (the run's own bare
  repository); a missing proposal or section gives an empty summary, never an
  error.
- `task` is the paused task when the pause names one, else the first unfinished
  task, else null.
- `recent` turns events into words: `landed` → "task 2.3 landed", `attempt_failed`
  → "task 1.6 attempt 2 failed", `gate_reviewed` → "section 1 gate review: FAIL"
  (or PASS), `arbitrated` → "arbiter ruled retry on task 1.6", `pause` → "paused:
  <reason>", `resumed` → "resumed". Other events are skipped.
- `github` is set only when the run has a PR (its branch is published) and the
  manifest's `run.github` names the repository.
- A failure building one run's context leaves that run's `context` with empty
  values; it must never fail the whole status document.

### Plugin

- `shared/factory.ts`: an optional `context` on each run, validated; strings are
  display text.
- `server/factory.ts`: projects `context` through the same `display()` redaction
  (home directory → `~`). The `github` link is passed only if it is an
  `https://github.com/` URL.
- `client/factory-model.ts`: pure functions for the Details content (which parts
  to show), the "more" command (`specloop status <change> --events 20`), and
  `matchWorkspace(repo, workspaces)` over SDK workspace descriptors: skip any
  entry with `archivingAt` set; write a leading `/Users/<name>` or
  `/home/<name>` segment of `workspaceDirectory` as `~`; the entry whose result
  equals the run's (already `~`-redacted) `repo` wins, the most recent
  `activityAt` first; none → null.
- `client/factory.tsx`: a "Details" toggle per row (collapsed by default) showing
  summary, repo and branch, task, full detail, recent events, the GitHub link
  (via the existing link pattern) and the command text. **Open in Paseo** appears
  only when the host provides `navigation.openWorkspace` and a workspace
  matches; workspaces come from `getPaseoClient(serverId).workspaces.list()`
  (its `entries`) for the Paseo host, fetched when Details is first opened.
  Any failure or a missing method hides the button.

## Risks / Trade-offs

- `fetchWorkspaces` and `navigation` belong to the Paseo client SDK 0.12; the
  button is hidden when either is unavailable rather than failing.
- Pause detail can carry diagnostic text; it is display text and is redacted
  like `why`.
