## Purpose

Correct native support guidance without modifying preset commands, templates or installation requirements.

## MODIFIED Requirements

### Requirement: Native build support guidance

The preset README SHALL describe explicit native drafting and builds using a
trusted `format = "speckit"` manifest and links to the engine guide and manifest
example. It SHALL distinguish asset installation from build configuration,
retain prerequisites and installation guidance, describe native front-door support under trusted-manifest enrollment, and leave unperformed live checks pending. It SHALL describe
skip-feasibility only as an optional existing bypass. This replaces the obsolete pending-front-door claim; preset assets and command behavior do not
change.

#### Scenario: Preset reader finds the native build path
- **WHEN** an operator reads the shipped preset README after installing its authoring assets
- **THEN** guidance points to explicit native drafting/admission/build/publication with a trusted native manifest, manifest-frozen specs/** and .specify/**, and Finish setting Status Complete without archival
- **THEN** installation alone does not enroll or configure a build; the v1.3.2 baseline, PyYAML/interpreter advice, checkout/release installation commands, issue-first example, house-rules/upstream links and pending scratch-install/live-reference checks on issue 184 remain intact
- **THEN** new checks use deterministic physical-line boundaries and resolving relative links, and all existing preset and plugin test assertions remain unchanged
