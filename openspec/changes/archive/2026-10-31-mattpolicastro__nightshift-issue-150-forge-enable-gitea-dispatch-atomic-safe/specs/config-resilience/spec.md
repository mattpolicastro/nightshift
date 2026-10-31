## Purpose

Retain repository-local health and foreign-journal holds while enabling Gitea stages outside those holds.

## ADDED Requirements



## MODIFIED Requirements

### Requirement: Health gates new work only

Repository configuration health SHALL gate new drafting and admission, including
spec-stage drafting, without consuming triggers or escalating issues. Admitted runs SHALL
retain publication, build revision and completion delivery using their own
manifest and recorded branch, except for Gitea enrollments under Foreign recovery journal hold. Later success
SHALL NOT clear unresolved errors. Pause, authorization, feedback-consumption and
durable-cancellation requirements SHALL remain in force.

#### Scenario: Unhealthy repository still publishes
- **WHEN** a GitHub-kind or eligible Gitea repository has an admitted daemon-managed run ready to publish with a valid copied manifest and safe recorded branch, and either its enrollment manifest is bad or missing or its repository spelling is unsupported for new names
- **THEN** the unpaused daemon publishes to its recorded PR and delivers human checks under existing publication rules, while retaining repository health failure and holding new work

#### Scenario: Unhealthy repository still takes build feedback
- **WHEN** the repository is GitHub-kind or eligible Gitea and an admitted run has authorized unconsumed build feedback and its enrollment is unhealthy
- **THEN** build-stage revision and successful consumption/delivery proceed on the recorded identity without reading the enrollment manifest or starting a spec draft

#### Scenario: Saved work survives a hold
- **WHEN** the repository is GitHub-kind or eligible Gitea and a dispatched draft or admission completes before or during a configuration hold
- **THEN** its job and result remain recoverable without redispatch; delivery requiring the unavailable template waits for repair without item escalation
- **THEN** an already-committed admission can complete notifications without another admit or healthy enrollment template

#### Scenario: Pause and stop still prevail
- **WHEN** an unhealthy repository has a publishable or revisable admitted run while the daemon is paused, or a pending job has durable stop intent
- **THEN** health bypass does not override pause or cancellation and no prohibited launch or delivery occurs

#### Scenario: Foreign journal overrides admitted delivery
- **WHEN** a Gitea enrollment has a foreign recovery journal hold and already-admitted work ready for publication, build-stage revision or completion delivery, whether its enrollment manifest is healthy or unhealthy
- **THEN** the daemon skips those stages as well as new work, retains pending results and receipts without consuming feedback or triggers, and reports the repository hold without item comments or label writes
- **THEN** repairing the manifest cannot override a foreign-journal hold; after operator inspection and clearing that gate, later otherwise eligible polls may recover saved work without redispatching completed jobs
- **THEN** other repositories continue, and the hold neither stops independent supervisor processes nor changes their admitted run state

### Requirement: Foreign recovery journal hold

A Gitea-enrolled repository SHALL never enter worker switch recovery. Any direct
engine-switch JSON journal at its existing local repository key, or an unreadable
journal location, SHALL hold all its daemon stages and escalate through repository
health without remote writes. Evidence SHALL remain intact and other repositories
SHALL continue.

#### Scenario: Gitea journal holds the repository
- **WHEN** a Gitea repository has a pending, completed or malformed direct engine-switch JSON journal at startup or on a later poll
- **THEN** recovery, drafting, admission, revision, publication and failure-report label writes are skipped for that repository without querying either forge
- **THEN** journals, claims, receipts and worktrees remain unchanged across restart, a nontransient repository health escalation identifies the inspection location, and healthy repositories continue

#### Scenario: Journal location is uncertain or clear
- **WHEN** a Gitea repository's journal location cannot be inspected, or is absent or contains no direct JSON journal
- **THEN** uncertainty holds and escalates without guessing; an absent, empty or nested-only location does not hold Gitea stages, and retired-worker recovery never runs
- **THEN** pause still prevents external work, and later confirmed removal of the active foreign journal by an operator permits otherwise eligible Gitea stages without automatic journal deletion, replay or migration
