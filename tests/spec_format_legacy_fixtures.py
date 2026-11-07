"""Frozen pre-format readers from db002b5 for bidirectional saved-run tests.

Manifest, parse, state dataclasses and _load are copied from that revision.
Manifest validation helpers and value types are shared: they were unchanged
by the format seam. These snapshots need neither Git history nor a deployed
run directory when tests execute. Do not update them with production schemas.
"""

import json
import time
from dataclasses import dataclass, field, fields
from pathlib import Path
from types import SimpleNamespace

from specloop.manifest import (
    ADAPTERS, DEFAULT_MUTATION_INVALID_STEPS, MutationRoute, Profile, Snapshot, Step,
    _coverage_step, _gates, _path, _pattern, _snapshot_refs, _snapshots, _steps,
)

@dataclass(frozen=True)
class Manifest:
    repo: Path  # local clone the harness owns; agents never get it
    change: str
    branch: str
    pr_base: str
    github: str
    roles: dict[str, Profile]  # the first profile of each chain
    allowed: tuple[str, ...]
    frozen: tuple[str, ...]
    verify: tuple[Step, ...]
    acceptance: tuple[Step, ...] = ()
    phase_gates: tuple[int, ...] = ()
    max_attempts: int = 3
    max_infra: int = 3
    review_invalid_retries: int = 2
    implement_minutes: int = 45
    review_minutes: int = 20
    idle_minutes: int = 10
    writable: tuple[str, ...] = ()
    deny_read: tuple[str, ...] = ()
    prepare_read: tuple[str, ...] = ()  # extra paths under $HOME that prepare steps may read (toolchains)
    vars: dict[str, str] = field(default_factory=dict)
    env_file: Path | None = None
    coverage_step: str = ""  # id of the verify step whose output lists executed scenario IDs
    coverage_exempt: dict[str, str] = field(default_factory=dict)  # scenario id -> who checks it instead
    chains: dict[str, tuple[Profile, ...]] = field(default_factory=dict)  # role -> fallback order
    evidence: bool = False  # gather coverage + mutation evidence at every section boundary
    mutation_steps: tuple[str, ...] = ()  # verify or mutation-only ids that build and test a mutant
    mutation_paths: tuple[str, ...] = ()
    mutation_count: int = 12
    mutation_min: float = 0.8
    gate_review: str = "human"  # "agent": a fresh section review at each phase gate; a human only if it fails twice
    stuck: str = "human"  # "agent": an arbiter rules once on a stuck task before a human is asked
    drills: tuple[dict, ...] = ()  # seeded defects for `specloop drill`
    prepare: tuple[Step, ...] = ()
    mutation_parallel: int = 2
    mutation_skip: tuple[str, ...] = ()  # literal, case-sensitive whole-line exclusions
    mutation_step: tuple[Step, ...] = ()
    mutation_routes: tuple[MutationRoute, ...] = ()
    auth_targets: tuple[str, ...] = ()  # all profiles, including unused and fallback logins
    mutation_invalid_steps: tuple[str, ...] = DEFAULT_MUTATION_INVALID_STEPS
    snapshots: tuple[Snapshot, ...] = ()



def parse(data: dict, roles_override: dict[str, str] | None = None) -> Manifest:
    """`roles_override` maps role -> profile name, for a per-run provider swap."""
    run = data["run"]
    profiles = {}
    for name, p in data.get("profiles", {}).items():
        if p["adapter"] not in ADAPTERS:
            raise ValueError(f"profile {name}: unknown adapter {p['adapter']!r}")
        auth = p.get("auth", "")
        if auth and (p["adapter"] != "codex" or p.get("base_url")):
            raise ValueError(f"profile {name}: auth applies only to a codex profile without base_url")
        profiles[name] = Profile(name, p["adapter"], p["model"], p.get("effort", ""), p.get("base_url", ""), auth)
    chains = {}
    role_names = {**data["roles"], **{k: v for k, v in (roles_override or {}).items() if v}}
    # `feasibility` is optional and falls back to the review chain. A refusal
    # gate is only as good as its model: on 2026-10-20 a local model refused
    # a feasible change, having passed the identical draft minutes earlier.
    for role in ("implement", "review", "feasibility", "arbiter", "gate"):
        if role in ("feasibility", "arbiter", "gate") and role not in role_names:
            continue
        names = role_names[role]
        names = [names] if isinstance(names, str) else list(names)
        for name in names:
            if name not in profiles:
                raise ValueError(f"role {role}: no profile {name!r}")
        if not names:
            raise ValueError(f"role {role}: no profile")
        chains[role] = tuple(profiles[n] for n in names)
    roles = {role: chain[0] for role, chain in chains.items()}
    for optional in ("feasibility", "arbiter", "gate"):  # all fall back to the review chain
        chains.setdefault(optional, chains["review"])
        roles.setdefault(optional, roles["review"])
    limits = data.get("limits", {})
    sandbox = data.get("sandbox", {})
    paths = data["paths"]
    verify = _steps(data.get("verify"), "verify")
    if not verify:
        raise ValueError("at least one verify step is required")
    prepare = _steps(data.get("prepare"), "prepare")
    acceptance = _steps(data.get("acceptance"), "acceptance")
    if {s.id for s in prepare} & {s.id for s in verify + acceptance}:
        raise ValueError("prepare step ids must not duplicate verify or acceptance step ids")
    mutation_step = _steps(data.get("mutation_step"), "mutation_step")
    collisions = {s.id for s in mutation_step} & {s.id for s in verify + prepare + acceptance}
    if collisions:
        raise ValueError(f"mutation_step ids collide with verify, prepare or acceptance: {sorted(collisions)}")
    variables = dict(data.get("vars", {}))
    for name in variables:
        if name.startswith("snapshot:"):
            raise ValueError(f"vars.{name} is reserved")
    snapshots = _snapshots(data.get("snapshot", []))
    ordinary = {k: v for k, v in variables.items() if k not in ("src", "build", "sha")}
    for snapshot in snapshots:
        _snapshot_refs(snapshot.warm, {snapshot.id}, f"snapshot {snapshot.id!r}.warm",
                       {k: v for k, v in ordinary.items() if k != "mutant_path"}, warm=True)
    for setting, steps in (("prepare", prepare), ("verify", verify),
                           ("acceptance", acceptance), ("mutation_step", mutation_step)):
        for step in steps:
            _snapshot_refs(step.run, {s.id for s in snapshots}, f"{setting} {step.id!r}.run", ordinary)
            _snapshot_refs(tuple(step.env.values()), {s.id for s in snapshots},
                           f"{setting} {step.id!r}.env", ordinary)
    if any("{mutant_path}" in arg for s in verify + mutation_step + prepare + acceptance
           for arg in (*s.run, *s.env.values())):
        if variables.get("mutant_path", "") != "":
            raise ValueError("vars.mutant_path is reserved and must be empty")
        variables["mutant_path"] = ""
    return Manifest(
        repo=_path(run["repo"]),
        change=run["change"],
        branch=run["branch"],
        pr_base=run.get("pr_base", "main"),
        github=run.get("github", ""),
        roles=roles,
        allowed=tuple(_pattern(p) for p in paths["allowed"]),
        frozen=tuple(_pattern(p) for p in paths.get("frozen", [])),
        verify=verify,
        prepare=prepare,
        acceptance=acceptance,
        mutation_step=mutation_step,
        snapshots=snapshots,
        phase_gates=tuple(int(n) for n in data.get("gates", {}).get("phases", [])),
        max_attempts=int(limits.get("max_attempts", 3)),
        max_infra=int(limits.get("max_infra", 3)),
        review_invalid_retries=int(limits.get("review_invalid_retries", 2)),
        implement_minutes=int(limits.get("implement_minutes", 45)),
        review_minutes=int(limits.get("review_minutes", 20)),
        idle_minutes=int(limits.get("idle_minutes", 10)),
        writable=tuple(sandbox.get("writable", [])),
        deny_read=tuple(str(_path(p)) for p in sandbox.get("deny_read", [])),
        prepare_read=tuple(str(_path(p)) for p in sandbox.get("prepare_read", [])),
        vars={k: str(_path(v)) if v.startswith("~") else v for k, v in variables.items()},
        env_file=_path(run["env_file"]) if run.get("env_file") else None,
        coverage_step=_coverage_step(data.get("coverage", {}), verify),
        coverage_exempt=dict(data.get("coverage", {}).get("exempt", {})),
        chains=chains,
        auth_targets=tuple(p.auth for p in profiles.values() if p.auth),
        **_gates(data.get("gates", {}), verify + mutation_step),
        drills=tuple(dict(d) for d in data.get("drill", [])),
    )



@dataclass
class TaskState:
    attempts: int = 0  # counted attempts (infra retries are not counted)
    infra: int = 0
    review_invalid: int = 0
    sessions: int = 0  # every agent session, counted or not; names attempt dirs
    status: str = "pending"  # pending | passed | accepted | stuck | blocked
    candidate: str = ""  # sha of the latest sealed candidate
    candidate_base: str = ""  # the branch head that candidate was built on
    pending_review: str = ""  # verified candidate whose review must (re)run
    feedback: str = ""  # what the next attempt is told
    violations: list[str] = field(default_factory=list)  # from the previous attempt
    waived: list[str] = field(default_factory=list)
    note: str = ""  # human note from `resume --note`, consumed by the next attempt
    auto_resumes: int = 0  # infra pauses the supervisor has lifted for this task
    arbitrated: int = 0  # times an arbiter ruled on this task being stuck (at most once)
    sealed: str = ""  # sealed commit whose verification has not finished (a killed driver re-verifies it)
    sealed_base: str = ""



@dataclass
class Pause:
    reason: str
    task: str = ""
    detail: str = ""
    resume_at: float = 0  # when set, `specloop serve` may lift the pause after this time



@dataclass
class RunState:
    change: str
    status: str = "running"  # running | paused | done | aborted
    pause: Pause | None = None
    tasks: dict[str, TaskState] = field(default_factory=dict)
    gates_passed: list[int] = field(default_factory=list)
    created: float = field(default_factory=time.time)
    coverage_rounds: int = 0
    acceptance_rounds: int = 0
    pr: str = ""
    exhausted: dict[str, float] = field(default_factory=dict)  # profile -> usable again at
    section_base: dict[str, str] = field(default_factory=dict)  # section -> head when its first task began
    evidenced: list[int] = field(default_factory=list)
    strengthen_rounds: dict[str, int] = field(default_factory=dict)
    evidence_summary: dict[str, str] = field(default_factory=dict)
    evidence_scores: dict[str, float] = field(default_factory=dict)  # section -> score before strengthening
    # Integration intent/verification: base, previous_head, merged_head,
    # verified_head, receipts, retry_verify. Keep base for evidence attribution.
    base_sync: dict = field(default_factory=dict)

    def task(self, key: str) -> TaskState:
        return self.tasks.setdefault(key, TaskState())



def _known(cls, data: dict):
    """Build from the fields this code knows, ignoring the rest.

    A field added by newer code must not crash an older reader: on 2026-10-20
    a supervisor still running yesterday's code died on a state.json that
    carried a new `arbitrated` count.
    """
    names = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in data.items() if k in names})



def _load(self) -> RunState:
    p = self.dir / "state.json"
    try:
        raw = json.loads(p.read_text())
    except FileNotFoundError:
        if self.require_existing:
            raise
        return RunState(change=self.dir.name)
    pause = _known(Pause, raw["pause"]) if raw.get("pause") is not None else None
    tasks = {k: _known(TaskState, v) for k, v in raw.get("tasks", {}).items()}
    return RunState(raw["change"], raw["status"], pause, tasks, raw.get("gates_passed", []), raw.get("created", 0),
                    raw.get("coverage_rounds", 0), raw.get("acceptance_rounds", 0), raw.get("pr", ""),
                    raw.get("exhausted", {}), raw.get("section_base", {}), raw.get("evidenced", []),
                    raw.get("strengthen_rounds", {}), raw.get("evidence_summary", {}),
                    raw.get("evidence_scores", {}), raw.get("base_sync", {}))



def load_state(directory):
    return _load(SimpleNamespace(dir=directory, require_existing=True))
