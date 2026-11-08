## Purpose

Preserve the command-line refusal/error boundary while removing npm-specific transient classification.

## MODIFIED Requirements

### Requirement: Validator availability classification

`specloop/openspec.py` SHALL define `ValidatorUnavailable(SpecError)`. The runtime version probe and operation SHALL raise it only for `subprocess.TimeoutExpired`. Launch errors, version failures and no-JSON results SHALL raise plain `SpecError`; parseable operation JSON SHALL retain existing interpretation. Each subprocess call SHALL use timeout 120 seconds with no in-process retry or backoff. A first operation SHALL perform the version probe before its operation call; successful probes SHALL be cached as specified by openspec-binary.

#### Scenario: Validator timeout is transient
- **WHEN** the version probe or OpenSpec operation raises `subprocess.TimeoutExpired`
- **THEN** the backend raises `ValidatorUnavailable`, also catchable as `SpecError`, with a command/timeout diagnostic and the timeout as its cause after one attempt at that subprocess with timeout 120
- **THEN** a timed-out version probe starts no operation, while a timed-out operation is not retried

#### Scenario: Registry failure is transient

This existing scenario name is retained for scenario-ID continuity. Its former npm-specific transient outcome is explicitly replaced below because standalone execution performs no registry acquisition; only subprocess timeouts remain transient.

- **WHEN** operation stdout cannot be decoded as JSON and stderr contains a standalone case-sensitive code `ENOTFOUND`, `ETIMEDOUT`, `ECONNRESET`, `EAI_AGAIN` or `ECONNREFUSED`
- **THEN** after one operation call the backend raises plain `SpecError`, never `ValidatorUnavailable`, including when the code occurs beyond the diagnostic truncation boundary, without retrying or using npm

#### Scenario: Other failures retain classification
- **WHEN** a no-JSON result contains none of the five standalone codes, including an unrelated error or a code embedded in a longer identifier
- **THEN** the backend raises plain `SpecError` with the existing bounded diagnostic rather than `ValidatorUnavailable`
- **WHEN** stdout contains parseable validation JSON with problems, even with a recognized code in stderr
- **THEN** the backend returns the parsed result and validation reports the problems normally

#### Scenario: Runtime fallbacks remain effective
- **WHEN** runtime scenario discovery raises `ValidatorUnavailable`
- **THEN** the existing `SpecError` handlers preserve scenario-lookup diagnostic text and the `gate_scenarios_failed` event
- **THEN** an evidence-gate lookup failure remains closed, without passed evidence or appended tasks, and requires no edits to `specloop/loop.py` or `specloop/evidence.py`
