# Changelog

## Native-worker draft review — 2026-09-08

Native execution remains disabled. Independent review found and fixed candidate
loss on evidence/shipping failures, ignored model reroutes, incomplete file-change
completion, and HTTP error classification. Failure-reporting errors cannot
reactivate destructive cleanup in the retained-candidate paths.

A synthetic integration test now joins isolated edits, host-owned commits,
exact-candidate verification and immutable reviewer source, with a failing
baseline control. Registered skills now have a reproducible remote read control
and host-path/symlink negatives. Characterization also records provider-home
discovery and executor-scratch symlink behavior; it does not claim package-local
confinement. Production launcher/lifecycle ownership, authentication/accounting
and daemon dispatch remain unfinished.

## Initial public release — 2026-09-08

Published from a sanitized source snapshot. Earlier development history and
operational records remain private.

Includes worktree-based task execution, independent review, verification and
push guards, recovery, endpoint routing, durable outcomes, and a Paseo attention
plugin with bounded refreshes. Merge remains a human decision.
