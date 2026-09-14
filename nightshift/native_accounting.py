"""Dormant native accounting evidence, separate from legacy scheduling telemetry.

A prepared claim identifies an attempt; it neither activates dispatch nor proves
billing. Missing usage stays unknown. No dollars, turns, subscription windows or
unbilled scheduling credit are derived from these token counts.
"""
import re
from dataclasses import dataclass
from pathlib import Path

from .workers.base import WorkerResult

_FIELDS = ('inputTokens', 'outputTokens', 'cachedInputTokens',
           'reasoningOutputTokens', 'totalTokens')


@dataclass(frozen=True)
class NativeTokens:
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int | None = None
    reasoning_output_tokens: int | None = None
    total_tokens: int | None = None

    def __post_init__(self):
        for value in self.__dict__.values():
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError('Native token counts must be nonnegative integers or unknown')

    @classmethod
    def from_usage(cls, usage: dict):
        if type(usage) is not dict or any(type(k) is not str or k not in _FIELDS for k in usage):
            raise ValueError('Unknown native usage shape')
        if any(type(v) is not int or v < 0 for v in usage.values()):
            raise ValueError('Reported native tokens must be exact nonnegative integers')
        return cls(*(usage.get(key) for key in _FIELDS))


@dataclass(frozen=True)
class NativeAccounting:
    run_id: str
    phase: str
    tokens: NativeTokens

    def __post_init__(self):
        if type(self.run_id) is not str or re.fullmatch(r'[0-9a-f]{32}', self.run_id) is None:
            raise ValueError('Native accounting requires an exact prepared run identity')
        if type(self.phase) is not str or self.phase not in ('implement', 'review'):
            raise ValueError('Native accounting requires an explicit phase')
        if type(self.tokens) is not NativeTokens:
            raise ValueError('Typed native token evidence is required')

    @property
    def key(self) -> tuple[str, str]:
        return self.run_id, self.phase


def from_worker(marker: dict, phase: str, result: WorkerResult) -> NativeAccounting:
    """Snapshot final reported usage; failed runs can still consume tokens.

    Unknown marker versions and usage shapes require explicit adapter updates.
    The marker is host-owned evidence, never a provider-supplied authorization.
    """
    if (type(marker) is not dict or set(marker) != {'version', 'run_id', 'recovery_dir'}
            or type(marker['version']) is not int or marker['version'] != 1
            or type(marker['recovery_dir']) is not str
            or not Path(marker['recovery_dir']).is_absolute()):
        raise ValueError('Native accounting requires a recognized prepared claim marker')
    if type(result) is not WorkerResult or result.runtime != 'codex-app-server':
        raise ValueError('Only native worker results may enter native accounting')
    return NativeAccounting(marker['run_id'], phase, NativeTokens.from_usage(result.usage))


def merge(existing: dict, incoming: dict) -> dict:
    """Idempotent final evidence merge; conflicting snapshots are not summed."""
    merged = {}
    for records in (existing, incoming):
        if type(records) is not dict:
            raise ValueError('Native evidence collection must be a dictionary')
        for key, value in records.items():
            if type(value) is not NativeAccounting or key != value.key:
                raise ValueError('Native evidence identity differs from its collection key')
            if key in merged and merged[key] != value:
                raise ValueError('Conflicting final native accounting evidence')
            merged[key] = value
    return merged
