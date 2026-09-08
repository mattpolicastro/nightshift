# Project instructions

Read README.md and BACKLOG.md before changing behavior.

- Workers never merge. The harness pushes branches; humans review and merge.
- Preserve credential isolation and the independent read-only reviewer.
- Read comments in worker.py before changing tool allow/deny lists.
- Missing verification or a missing reviewer verdict is not success.
- Never commit credentials, local config, or worker transcripts.
- Run uv run pytest -q for Python changes. For plugin changes, run npm ci
  --ignore-scripts, npm run typecheck, and npm test in plugins/nightshift.
- Do not restart a running daemon without authorization.
