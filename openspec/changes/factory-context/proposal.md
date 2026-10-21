## Why

The Factory section in Paseo shows what a paused run needs, but someone arriving
cold cannot tell what the change is, where its code lives, which task it is on,
or what just happened, and has nowhere to click for more. Matt asked for links
and context on each run (2026-10-21).

## What Changes

- `specloop status --json` gains a `context` object for every run: the first
  paragraph of the change's "Why", the repository and branch, the current or
  paused task, the full pause detail, the last five events in plain words, and a
  GitHub link to the change's folder once the branch is published (the run has
  a PR and the manifest names its GitHub repository).
- The Nightshift plugin passes that context through (display fields only, home
  directory as `~`) and each Factory row gains a collapsed **Details** section
  showing it, an **Open in Paseo** button that opens the Paseo workspace for the
  run's repository, the GitHub link, and the command that shows more on the
  Studio.

Out of scope: editing specs from the page; links for repositories that have no
Paseo workspace or no published branch (the affordance is simply absent).

## Capabilities

### New Capabilities

- `run-context`: per-run context in status JSON and its presentation in the
  Factory section.

## Impact

`specloop/cli.py` (status JSON), `plugins/nightshift/{shared,server,client}`
factory files and tests, `tests/test_specloop_status_json.py`, the plugin
README. No new dependencies; the plugin's version range is unchanged.
