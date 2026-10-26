## Purpose

Preserve trusted host command configuration through the CLI rename.

## MODIFIED Requirements

### Requirement: Plugin host command configuration

Both plugin execution adapters and the attention installation check SHALL use server-owned host path configuration. Configuring paths SHALL preserve argv-based execution without a shell, existing RPC validation and display projection, and command lifecycle behavior.

#### Scenario: Checkout comes from configuration
- **WHEN** attention, Factory status and an allowed Factory action execute with a nondefault checkout and uv path
- **THEN** their production execFile calls receive the configured executable and exact cwd, and attention checks installation under that checkout
- **THEN** Factory uses nightshift in place of specloop, retaining all other command arguments; attention uses `<bin>:/usr/bin:/bin:/usr/sbin:/sbin` and Factory uses `<node>:<bin>:/usr/bin:/bin:/usr/sbin:/sbin` with the configured directories
- **THEN** existing timeouts, output limits, caches and child cleanup remain unchanged

#### Scenario: RPC cannot select host commands
- **WHEN** a request contains extra path-like fields or a note with shell metacharacters
- **THEN** it cannot override host settings, no shell is invoked and an allowed note reaches the CLI as one literal argument
- **THEN** unknown changes and unavailable verbs still fail without spawning an action
- **THEN** only validated display fields cross the RPC boundary, with existing home-directory redaction

#### Scenario: Configuration failures preserve plugin errors
- **WHEN** a consumed host setting is invalid or the configured checkout/executable is unavailable
- **THEN** the plugin uses its existing unavailable, stale-cache or refused-action response as applicable, without exposing raw host paths or errors or reporting a healthy empty list

