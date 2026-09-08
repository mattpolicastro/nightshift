# Task outcomes and Paseo

Nightshift records durable task outcomes locally. `nightshift attention --json`
returns outstanding tasks and stale records; `nightshift outcomes` exposes the
ledger. GitHub is the source for reconciliation of issue and PR resolution.

The optional Paseo plugin presents one host-level attention page. It exposes
only display fields and validated GitHub links, not transcripts or commands.
See plugins/nightshift/README.md for installation and refresh limits.
