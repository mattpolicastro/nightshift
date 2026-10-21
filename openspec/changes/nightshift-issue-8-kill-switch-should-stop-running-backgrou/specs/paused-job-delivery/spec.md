## Purpose

Keep daemon scheduling and completion delivery held while the Nightshift pause
file exists, preserving completed results for delivery after resume.

## ADDED Requirements

### Requirement: Pause gates launches and delivery

While `~/.nightshift/pause` exists, the daemon SHALL start no new background job
and initiate no external completion-delivery operation. It SHALL recheck pause
immediately before launches and external operations, including delivery retries.
Existing jobs MAY finish and write local results; pause SHALL not implicitly
terminate them. Undelivered results SHALL remain recoverable and SHALL not be
marked delivered while paused.

#### Scenario: Pause before launch
- **WHEN** pause exists at poll entry or appears after intake reads but immediately before launching a job
- **THEN** the daemon launches no new job, changes no delivery labels and does not count the pause as a failure

#### Scenario: Admission finishes while paused
- **WHEN** an already-running admission finishes after pause is created and the daemon polls again while paused
- **THEN** its local result remains available, no completion comment or label change occurs, no publication starts, and its receipt is not marked delivered

#### Scenario: Draft or revision finishes while paused
- **WHEN** an already-running draft or revision finishes while pause exists
- **THEN** the daemon retains its result and initiates no delivery push, PR creation, comment, label change or publication until resume

#### Scenario: Pause during delivery
- **WHEN** pause appears between two external operations in a completion delivery or delivery retry
- **THEN** the next operation is withheld and delivery remains incomplete, while any operation already dispatched may finish

### Requirement: Resume pending delivery

After pause is removed, the daemon SHALL resume eligible unstopped completion
delivery from durable state using existing idempotency markers, without
relaunching the completed job or duplicating completed delivery effects.

#### Scenario: Resume once after restart
- **WHEN** a job finishes while paused, the daemon restarts, pause is removed and successive polls run
- **THEN** the daemon delivers its retained result once without another job launch, duplicate PR or duplicate completion comment, and marks delivery complete

### Requirement: Independent supervisor boundary

Nightshift pause and jobs stop SHALL apply to daemon actions and tracked job
groups respectively. They SHALL not alter already-committed specloop run state
or claim to stop independent supervisor processes. README SHALL document this
boundary and the fact that unloading the daemon leaves detached jobs running.

#### Scenario: Admission already committed
- **WHEN** an admission has committed runnable specloop state before pause or job termination
- **THEN** Nightshift leaves that run state intact, withholds its own delivery while paused or cancelled, and documents that the separate supervisor may continue or start the build
