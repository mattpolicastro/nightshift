---
description: Create an issue-derived feature specification with stable scenario labels.
strategy: wrap
---

## Issue-first preamble

Apply these instructions before any embedded core step, including `before_specify`
hooks. This preamble controls identity; the composed core still owns its workflow.

1. Read the first whitespace-delimited argument token as a full match of
   `#?[0-9]+`. Remove an optional `#`, canonicalize leading zeros as decimal,
   and require a positive number and nonempty remaining title. Otherwise refuse
   before creating branches or feature files: show usage
   `/speckit.specify 42 Add a gate` (also `/speckit.specify #42 Add a gate`) and
   ask the author to open an issue and supply its positive number and title.
   Treat the remaining title as text, never shell code; strip the issue token
   from effective core feature-description arguments, including `$ARGUMENTS`.
   Do not look up an issue through a forge API or infer its number.
2. Pad the decimal issue number to at least three digits; never truncate.
   Lowercase the title, replace runs outside ASCII `a-z0-9` with `-`, strip
   boundary hyphens, take the first 40 characters, strip trailing hyphens, and
   use `issue` if empty. Do not transliterate or invent a semantic short name.
   The exact identity is `specs/<NNN>-<slug>` and branch `spec/<NNN>-<slug>`:
   `42 Add a gate` gives `specs/042-add-a-gate` and `spec/042-add-a-gate`;
   `1234 Big feature` gives `specs/1234-big-feature` and `spec/1234-big-feature`.
3. Before hooks, reject conflicting explicit user directory/branch overrides;
   show the calculated values and ask for matching overrides or their removal.
   Refuse if the feature directory already exists; identify it and direct the
   author to a separate draft-editing action. Never overwrite an existing record.
   Calculated identity overrides stale feature state, sequential/timestamp
   configuration and core short-name suggestions; never allocate a fallback
   directory or a second spec file.
4. Supply `SPECIFY_FEATURE_DIRECTORY=specs/<NNN>-<slug>` explicitly to every
   relevant shell/helper invocation and nested hook, before hooks and core
   resolution. An `export` in an earlier separate tool process is insufficient.
   When the existing Git extension before-specify hook is enabled, also supply
   `GIT_BRANCH_NAME=spec/<NNN>-<slug>` explicitly to that hook. Let the hook own
   branch creation; do not create it twice. Without that hook, require the author
   to select the exact branch before continuing; refuse a mismatch and report
   the expected branch so the author can create/select it and retry.
5. After hooks and before core spec creation, verify the actual Git branch still
   matches. Refuse unavailable Git, branch-creation failure or mismatch with an
   actionable diagnostic naming the expected branch and the failed prerequisite;
   create no feature files. Preserve the composed core's template resolver,
   validation, handoffs, hooks and persistence of the actual resolved directory
   in `.specify/feature.json`. Execute it with the selected overrides and title.

{CORE_TEMPLATE}
