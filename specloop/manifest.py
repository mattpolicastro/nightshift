"""The trusted manifest: what a run may touch, how it is verified, who does the work.

Lives outside the target repo so no agent can edit it. `admit` copies it into
the run directory and the run only ever reads that copy.
"""

import json
import tomllib
from dataclasses import dataclass, field
from datetime import date, datetime, time
from pathlib import Path

ADAPTERS = ("codex", "claude")


@dataclass(frozen=True)
class Profile:
    name: str
    adapter: str
    model: str
    effort: str = ""
    base_url: str = ""  # empty: the CLI's own subscription login


@dataclass(frozen=True)
class Step:
    id: str
    run: tuple[str, ...]
    expect: int = 0
    timeout: int = 300


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
    mutation_steps: tuple[str, ...] = ()  # verify step ids that build and test a mutant
    mutation_paths: tuple[str, ...] = ()
    mutation_count: int = 12
    mutation_min: float = 0.8
    gate_review: str = "human"  # "agent": a fresh section review at each phase gate; a human only if it fails twice
    stuck: str = "human"  # "agent": an arbiter rules once on a stuck task before a human is asked
    drills: tuple[dict, ...] = ()  # seeded defects for `specloop drill`
    prepare: tuple[Step, ...] = ()
    mutation_parallel: int = 2
    mutation_skip: tuple[str, ...] = ()  # literal, case-sensitive whole-line exclusions


def _path(s: str) -> Path:
    return Path(s).expanduser()


def _pattern(p: str) -> str:
    # An exact path, or `dir/**`. No other globbing.
    body = p[:-3] if p.endswith("/**") else p
    if not body or body.startswith("/") or any(c in body for c in "*?[") or ".." in body.split("/"):
        raise ValueError(f"bad path pattern {p!r}")
    return p


def _steps(raw) -> tuple[Step, ...]:
    steps = []
    for s in raw or []:
        run = s["run"]
        if not isinstance(run, list) or not run or not all(isinstance(a, str) for a in run):
            raise ValueError(f"step {s.get('id')!r}: run must be a non-empty argv list")
        steps.append(Step(s["id"], tuple(run), int(s.get("expect", 0)), int(s.get("timeout", 300))))
    ids = [s.id for s in steps]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate step ids")
    return tuple(steps)


def parse(data: dict, roles_override: dict[str, str] | None = None) -> Manifest:
    """`roles_override` maps role -> profile name, for a per-run provider swap."""
    run = data["run"]
    profiles = {}
    for name, p in data.get("profiles", {}).items():
        if p["adapter"] not in ADAPTERS:
            raise ValueError(f"profile {name}: unknown adapter {p['adapter']!r}")
        profiles[name] = Profile(name, p["adapter"], p["model"], p.get("effort", ""), p.get("base_url", ""))
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
    verify = _steps(data.get("verify"))
    if not verify:
        raise ValueError("at least one verify step is required")
    prepare = _steps(data.get("prepare"))
    acceptance = _steps(data.get("acceptance"))
    if {s.id for s in prepare} & {s.id for s in verify + acceptance}:
        raise ValueError("prepare step ids must not duplicate verify or acceptance step ids")
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
        vars={k: str(_path(v)) if v.startswith("~") else v for k, v in data.get("vars", {}).items()},
        env_file=_path(run["env_file"]) if run.get("env_file") else None,
        coverage_step=_coverage_step(data.get("coverage", {}), verify),
        coverage_exempt=dict(data.get("coverage", {}).get("exempt", {})),
        chains=chains,
        **_gates(data.get("gates", {}), verify),
        drills=tuple(dict(d) for d in data.get("drill", [])),
    )


def _gates(g: dict, verify: tuple[Step, ...]) -> dict:
    skip = g.get("mutation_skip", [])
    if not isinstance(skip, list) or any(not isinstance(p, str) or not p for p in skip):
        raise ValueError("gates.mutation_skip must be an array of nonempty strings")
    parallel = g.get("mutation_parallel", 2)
    if type(parallel) is not int or parallel <= 0:
        raise ValueError(f"gates.mutation_parallel must be a positive integer, not {parallel!r}")
    steps = tuple(g.get("mutation_steps", []))
    unknown = set(steps) - {s.id for s in verify}
    if unknown:
        raise ValueError(f"mutation_steps names unknown verify steps: {sorted(unknown)}")
    for key in ("review", "stuck"):
        if g.get(key, "human") not in ("human", "agent"):
            raise ValueError(f"gates.{key} must be \"human\" or \"agent\", not {g[key]!r}")
    return {"evidence": bool(g.get("evidence", False)), "mutation_steps": steps,
            "mutation_parallel": parallel,
            "mutation_skip": tuple(skip),
            "gate_review": g.get("review", "human"), "stuck": g.get("stuck", "human"),
            "mutation_paths": tuple(_pattern(p) for p in g.get("mutation_paths", [])),
            "mutation_count": int(g.get("mutation_count", 12)), "mutation_min": float(g.get("mutation_min", 0.8))}


def _coverage_step(cov: dict, verify: tuple[Step, ...]) -> str:
    step = cov.get("step", "")
    if step and step not in {s.id for s in verify}:
        raise ValueError(f"coverage step {step!r} is not a verify step")
    return step


def load(path: Path, roles_override: dict[str, str] | None = None) -> Manifest:
    with open(path, "rb") as f:
        return parse(tomllib.load(f), roles_override)


def _toml_value(value) -> str:
    """Encode parsed TOML, including nested tables and arrays of tables."""
    if isinstance(value, dict):
        return "{ " + ", ".join(f"{_toml_value(k)} = {_toml_value(v)}" for k, v in value.items()) + " }"
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, float):
        return repr(value)  # TOML uses lowercase inf/nan, unlike JSON.
    return json.dumps(value, ensure_ascii=False).replace("\x7f", "\\u007f")


def template(path: Path, roles_override: dict[str, str] | None = None, *,
             change: str | None = None, branch: str | None = None) -> tuple[Manifest, str]:
    """Load a template and return the effective manifest and its storable TOML."""
    text = path.read_text()
    data = tomllib.loads(text)
    overrides = {k: v for k, v in {"change": change, "branch": branch}.items() if v is not None}
    if overrides:
        data["run"].update(overrides)
        text = "\n".join(f"{_toml_value(k)} = {_toml_value(v)}" for k, v in data.items()) + "\n"
    return parse(data, roles_override), text


def matches(path: str, patterns: tuple[str, ...]) -> bool:
    for p in patterns:
        if p.endswith("/**"):
            if path.startswith(p[:-2]):
                return True
        elif path == p:
            return True
    return False
