## Purpose

Explain optional trusted dependency snapshots, their preparation fallback and the limits of automated validation.

## ADDED Requirements

### Requirement: Snapshot preparation guidance

`docs/specloop/prepare.md` SHALL document snapshot configuration, trusted warming, private consumption, fallback and retention. It SHALL preserve existing guidance, provide a structurally validated example with empty-cache fallback, cite issue #134 and identify scheming-vizier timing and relocation validation as pending human checks.

#### Scenario: Operator configures a cache with fallback
- **WHEN** the documented snapshot fragment is extracted by the design's exact physical-line marker rules and loaded with the real manifest parser
- **THEN** fields and references parse, local fixture execution demonstrates distinct warm/base and prepare/candidate sources plus hit and empty-cache behavior, and ambiguous markers or fences block validation
- **THEN** guidance explains dependency-only Dart caching and regeneration of candidate project configuration, does not claim CI proved a speedup, and keeps human-only comparisons outside implementation tasks
- **THEN** it names all manifest fields, lockfile identity, base-sync-only warming, payload output, clone placeholders, unconditional candidate preparation, fallback events, APFS limits and the three-entry total retention rule
