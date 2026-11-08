## Context

The landed #187 seam delegates to `specloop.openspec` at call time. Its backend currently runs a pinned npm package for validate/show/archive, with telemetry/update checks disabled and a 120-second subprocess timeout. #179 introduced `ValidatorUnavailable(SpecError)`, including npm network codes; admission and drafting distinguish that subclass from ordinary refusals. Those exception handlers and the format seam need no production edits.

`nightshift/preflight.py:run` aggregates independent `Check` results for credentials, forges, alerts and retired deep probes. `nightshift/host_paths.py:host_path` already supplies literal, trusted path resolution without filesystem probing. The plugin's Factory PATH contains the configured Node directory, whereas `bin/specloop-serve` hardcodes `~/n/bin` specifically for OpenSpec.

Existing requirements explicitly preserving npx invocation and npm transient classification are amended in this change. The supplied frozen-path rule prohibits every `bin/**` edit, including comments; a design or spec delta cannot override that rule. The original request to remove the supervisor PATH prefix is therefore deferred. Worker-retirement service requirements and all other requirements remain in force.

## Goals / Non-Goals

Provide deterministic OpenSpec 1.16.1 execution without npm or network acquisition, distinguish installation mistakes from retryable timeouts, and make preflight establish the installed executable's integrity. Preserve strict validation, JSON interpretation, scenario identity, archive task gating, existing admission refusal/error envelopes, environment flags and format adapter boundaries.

Do not implement spec-kit, alter admission/drafting retries, add a validator parser, fetch tools in application code, broaden Nightshift's macOS host support, relocate wrappers, edit any `bin/**` file or launchd plist, or repair enrolled repositories' specifications. Upstream Linux/Windows builds do not establish Nightshift support for those hosts.

## Decisions

### Executable resolution and version gate

Keep executable helpers in `specloop/openspec.py`; reuse `nightshift.host_paths.host_path` without changing that module. Expose a small resolver accepting an optional environment mapping so runtime uses `os.environ` and preflight uses its supplied `env`. Resolve `NIGHTSHIFT_OPENSPEC_BIN` first; only when absent resolve `NIGHTSHIFT_BIN_DIR` (default `/opt/homebrew/bin`) and append `/openspec`. An explicit executable must not evaluate an unused bin-directory setting. Here `OPENSPEC_BIN` names the resolved value used in argv, not an import-time environment snapshot.

Honor the existing literal path rules: absolute or leading `~/`, spaces/metacharacters preserved, no shell evaluation, and invalid consumed settings refused by name without echoing their values. Never search PATH or consume candidate/RPC configuration. Convert resolver errors to plain `SpecError` at the backend boundary.

The only executable version constant is `VERSION = "1.16.1"`; preflight imports it. Before the first operation for a resolved path, run `[OPENSPEC_BIN, "--version"]`, without `--json`, with the same cwd, merged `ENV`, output capture and 120-second timeout as operations. Require exit zero and stdout stripped of surrounding whitespace equal to `VERSION`; do not accept a substring, another release or a banner containing the release. Use one cached successful check per resolved path per process. Failed checks are not cached, so a later invocation can observe operator repair; no check retries within an invocation. A changed executable path must get its own check. Concurrent first use must not duplicate a successful probe. Restart consumers after replacing a binary at an already-checked path.

After the successful gate, `_run(repo, *args)` calls exactly `[OPENSPEC_BIN, *args, "--json"]`; validate is `[OPENSPEC_BIN, "validate", CHANGE, "--strict", "--json"]`. The first successful use has two subprocess calls; later operations have one. Keep `ENV` unchanged. Both calls use timeout 120, so the first operation can spend up to two such subprocess budgets; do not claim a combined 120-second deadline. Perform no runtime checksum read.

Catch missing/execution-denied/other launch `OSError` as plain `SpecError`. Missing binary, unusable version output and version mismatch diagnostics name the pinned version and `docs/deploy.md#install-openspec`, directing installation at the configured path. Any `TimeoutExpired`, including the version probe, becomes `ValidatorUnavailable` with cause and command/timeout context. Remove the npm network-code heuristic: every non-JSON operation result is plain `SpecError`, retaining its existing bounded diagnostic. Parseable JSON, even on nonzero exit, follows the existing interpretation. Existing admission handlers turn missing-binary `SpecError` into `outcome: refused` without admitted state; timeout handling retains #179's version-1 error envelope without in-process retry.

### Preflight and checksum provenance

Add a single named `Check("openspec", ...)` in every `nightshift.preflight.run`, independent of forge checks and `deep`. Use a focused helper and injectable subprocess runner for offline tests; do not reuse the GitHub runner's unbounded call. Read the same configured executable path, require a readable regular executable file (symlinks to one are allowed), hash its bytes, then invoke the version probe with a 120-second timeout and the existing flags. Check integrity before executing an unverified file. Preflight performs a fresh check each invocation and never populates or trusts the runtime version cache.

Commit release data beside the backend as `specloop/openspec-SHA256SUMS` and ensure it is available beside the installed module. Do not compare an extracted executable with an archive digest. Use deterministic GNU-style lines `<64 lowercase hex><two spaces><artifact key>`; ignore only blank lines and lines starting with `#`. Reject malformed or duplicate keys. Executable entries use `binary/<upstream target>/openspec`; archive entries use `archive/<exact release asset filename>`. The host-target map SHALL contain only `(platform.system(), platform.machine()) == ("Darwin", "arm64")` mapping to `darwin-arm64`, the Mac Studio host. Every other pair fails closed as unknown, including Intel macOS, Linux and Windows; archive entries for those releases do not authorize execution or establish host support.

The implementer has no network. Task 1.2 copies the following supplied release evidence, without fetching assets or deriving additional digests. The operator verified it on 2026-11-08 from https://github.com/Fission-AI/OpenSpec/releases/tag/v1.16.1, published 2026-11-07T00:44:30Z. The official `SHA256SUMS` is reproduced verbatim here:

```text
30e9f4b0a6ca4d9025a3c78a85343e15f60fa03ce042c358d6667d177a03ee19  openspec-darwin-arm64.tar.gz
9c8fb2cec0b118f0dddd9db2455ee8c46fc458ccc60aa20230e1baeee85549a5  openspec-darwin-x64.tar.gz
e72004a0ef31c8c32868a729fb3ce4e401b97ec7034dabdf5262aa10f6472b33  openspec-linux-arm64-musl.tar.gz
06d2a38a322b4da993500edd6726a3b554adfe565d7e4834d140f72102c5c0b3  openspec-linux-arm64.tar.gz
c355b8d4b06b5734330f2e098d8c9fc1f3cf8b0b5fa1573f30d91e710a934e56  openspec-linux-x64-musl.tar.gz
9e5215b8f9a7024fa635f693a6a691d82220703868f1949927e675b87503c608  openspec-linux-x64.tar.gz
b9a0d9eee8c36c06fab86bd89cd87138855b29a3afd2e47346991f8787a823b1  openspec-windows-arm64.zip
6c00846a7e4813366ff8395cef71cadcc11d66eb7a112fe332358f8de5ae230e  openspec-windows-x64.zip
```

Copy all eight archive digests unchanged into manifest entries prefixed with `archive/`. Add exactly one installed-binary entry:

```text
bf0fbb541e79c3990906254be7d1b075c95efceb41e9e28899e72357ba4268a0  binary/darwin-arm64/openspec
```

Manifest comments must record the release URL, publication timestamp, operator verification date and this derivation: `openspec-darwin-arm64.tar.gz` passed `shasum -a 256 -c` against the official list (OK), extracted to `LICENSE` and `openspec`, and `shasum -a 256 openspec` produced the binary digest above. Link the derivation to `archive/openspec-darwin-arm64.tar.gz`. Record that `./openspec --version` printed exactly `1.16.1` with exit 0, whereas `./openspec version` printed `OpenSpec 1.16.1 (standalone)`; the runtime uses `--version`, so its exact-match rule holds. These are supplied operator observations, not checks performed by the implementing agent. Additional host targets require separately reviewed binary provenance and a map update.

Missing/unreadable/non-executable files, unknown targets, absent or invalid checksum data, wrong hashes, version mismatch, failed probes and timeouts all yield a failed preflight `Check` with the install instruction. An installation failure must not suppress other results or claim readiness. No downloads, chmod, replacement or repair occur in preflight. Tests use small fake executable files and fixture checksum data, never the developer's installed OpenSpec or a live registry.

### Strict behavior and preserved lint

Delegate 1.16.1 parsing to the pinned binary. Under `--strict`, scenarios lacking both WHEN and GIVEN, or lacking THEN, fail; a requirement under an unrecognized `## … Requirements` section fails; valid closing hash runs on headings are accepted. Continue forwarding structured issues unchanged into normal refusal/correction behavior.

Retain the harness's exact physical-line rule in `validate`: scan each live change spec with `splitlines()` and reject lines matching `^#{1,3}\s+Scenario:`. Do not suppress matches because they appear in fences, HTML, examples or indentation contexts; no semantic parsing is introduced. Ambiguous matching text blocks admission. Keep duplicate-ID checks, the four-hash authoring convention, unfinished-task archive refusal and exclusion of archived changes from harness validation.

Offline subprocess fixtures establish argv, version gates, issue forwarding and local lint; they do not prove the external parser. The real pinned validator compatibility runs below supply that evidence without imposing downloads or a host installation on `uv run pytest`.

### Supervisor, plugin and documentation boundaries

Treat all `bin/**` files and launchd plists as read-only. In particular, preserve `bin/specloop-serve` byte-for-byte, including its `$HOME/n/bin:` PATH prefix and stale OpenSpec/Node comment. Do not add another wrapper, relocate it, or change a freeze rule to bypass this boundary. The absolute executable selection removes the backend's dependency on that prefix without editing the wrapper. Removing the prefix and comment requires separately authorized work and is not an implementation or completion requirement here.

In `plugins/nightshift/server/factory.ts` and `host-paths.ts`, explain that the configured Node prefix serves the plugin's own Node tools; retain `NIGHTSHIFT_NODE_BIN`, its default and Factory PATH ordering. Update the obsolete assertion message in `plugins/nightshift/tests/factory.test.ts` without weakening its assertion.

Add `## Install OpenSpec` in `docs/deploy.md`: for the supported `darwin-arm64` host, download `openspec-darwin-arm64.tar.gz` and official SHA256SUMS over HTTPS from the v1.16.1 release, verify before extracting, install the executable at `<NIGHTSHIFT_BIN_DIR>/openspec` (or the explicit override), set executable permissions, run `--version` and `nightshift preflight`, then restart affected consumers in the existing quiet-window order. Include reproducible operator commands using the supplied release evidence above; implementing agents do not run downloads. Explain the two digest types, the sole supported checksum target and fail-closed behavior elsewhere, no runtime acquisition/fallback, and rollback requiring matching code/binary pins.

Necessary shared-file exceptions are `README.md` (prerequisites/host setup/unchanged wrapper caveat), `docs/specloop/README.md` (validator/formats and host guidance), `docs/specloop/command-json.md` (obsolete npx guarantee and timeout/install distinction), and `KNOWN-LIMITATIONS.md` (host table, npx manual-check wording and unchanged wrapper caveat). Preserve concurrent #182 readiness and #185 spec-kit text; rebase narrowly instead of restoring old format-availability prose. Also correct `plugins/nightshift/README.md` and only the obsolete npx explanatory docstring in `specloop/propose.py`; no executable code there changes.

The frozen wrappers retain obsolete npx commentary: explain this explicitly in the active guides instead of claiming all repository mentions were corrected. Preserve the current unconditional wrapper-freeze guidance; distinguish dependency removal from deferred wrapper cleanup.

Avoid all other in-flight files: `checklists/readiness.md`, `docs/specloop/speckit.md`, `examples/specloop/self-hosting.toml`, `nightshift/specloop_intake.py`, `presets/**`, `specloop/speckit.py`, `tests/fixtures/speckit/**`, `tests/merge_readiness_fixtures.py`, `tests/test_forge_gitea.py`, `tests/test_merge_readiness_evidence.py`, `tests/test_speckit_format.py`, `tests/test_specloop_base_publication.py` and `tests/test_specloop_documentation_integrity.py`. Put any new documentation checks in `tests/test_openspec_binary_docs.py`.

For any structural documentation selection/check, use physical lines: an exact, column-zero `## Title` start through the next column-zero `## ` line or EOF. Require exactly one start; block on absent, duplicate or whitespace-indented lookalikes. Table rows are physical lines beginning with `|` within that range. Treat fences and HTML literally; do not add a Markdown parser or assert exact prose. Ordinary reviewed paragraph edits need no new structural test.

### Tests and existing assertions

Task 1.1 includes the required named tests in `tests/test_specloop_command_json.py`: `test_validate_argv_uses_pinned_binary`, `test_missing_binary_is_not_transient`, `test_version_mismatch_refuses` and `test_timeout_is_transient`. Adapt `validator_process` to route the selected binary and distinguish `--version` from operations, reset process-cache state between tests, and keep unrelated subprocess calls real. Migrate the existing adapter argv assertions in `tests/test_spec_format.py` and validator stubs in `tests/test_specloop_critic.py` only if needed. Preserve all unrelated assertions.

There is one necessary expectation change beyond argv: existing npm-code tests currently assert `ValidatorUnavailable`. Preserve their code/prefix/beyond-truncation cases and diagnostics, but assert plain `SpecError`, as required by timeout-only classification; the corresponding requirement is explicitly MODIFIED. Retain all four existing scenario identities, including `Registry failure is transient`, and its test marker. That historical title now covers an explicitly changed outcome: stderr registry codes alone are ordinary errors because the binary performs no npm acquisition. Do not silently drop or rename that scenario, or retain its obsolete transient expectation alongside the new timeout-only rule. Keep timeout cause, timeout/environment, admission/drafting envelopes, correction limits and late-binding assertions. Fixtures may model a valid version handshake without removing operation assertions.

Task 1.2 adds `tests/test_preflight.py::test_openspec_binary_checksum_and_version`, including a fake executable with an incorrect digest and a correctly matching fixture. Existing readiness tests must supply a valid fake prerequisite instead of weakening `all(check.ok ...)` or CLI exit assertions. Verify missing/non-executable/wrong-version/unknown-target/malformed-manifest failures, independence from runtime cache, no execution on a hash mismatch, and preservation of other preflight checks. No live host tools are required.

## Risks / Trade-offs

The version cache cannot detect replacement at the same path during a running process; preflight and consumer restarts are the operational boundary. A digest verifies agreement with committed release data, not an independent upstream signing authority. Derived executable hashes require reviewable archive provenance. Do not bless locally installed arbitrary binaries by hashing them into the manifest.

The stricter validator can reject existing live specs or drafts; discover and report failures before merge/deploy without repairing those repositories in this change. Archived historical failures are informational because the harness does not validate archives. Keeping the Node directory for the plugin avoids conflating its own requirements with OpenSpec removal. The bundled supervisor also retains that directory and its stale comment because `bin/**` is frozen. Absolute executable selection plus the Mac Studio cut-over establish removal of the backend dependency; no separate supervisor-without-`~/n/bin` experiment is required. Exact bundled-wrapper cleanup remains deferred on #184's ledger, not completed by this change. Supporting only `darwin-arm64` lets offline implementation use the supplied verified evidence; other hosts intentionally fail preflight until reviewed binary evidence is added.

## Human checks

- [ ] Before merge, use verified OpenSpec 1.16.1 to run `openspec validate --all --strict` on a throwaway clone of Nightshift `main` and the Scheming Vizier checkout. In a separate disposable fixture root, also exercise strict-validation examples missing WHEN/GIVEN, missing THEN and using an unrecognized requirements header; verify failure, then verify a valid four-hash scenario with closing heading hashes passes. Record repository revisions, executable version/digest, command exits, example outcomes and every failing path/diagnostic on #191, linked from tracking issue #184. Distinguish active failures from informational archived history; do not edit those repositories' spec files in this change. Fixture tests alone do not prove upstream parser behavior.
- [ ] Before deploy, repeat `openspec validate --all --strict` against both enrolled OpenSpec repositories' intended deployment checkouts and record their revisions and failures on #191/#184.
- [ ] In a quiet window on the Mac Studio, install the verified 1.16.1 executable, run preflight, restart affected consumers, admit one real OpenSpec change and finish one through the deployed service path. Preserve normal finish human gates and confirm the plugin still resolves its own Node tools. Record executable/version/digest, service PATH, run identities and outcomes on #191/#184; explicitly record that the bundled wrapper remains unchanged. CI cannot establish this live cut-over.
