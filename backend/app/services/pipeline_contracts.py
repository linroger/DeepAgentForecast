"""Immutable observations for the advisory pipeline reuse planner.

These are caller-supplied claims, not authenticated producer receipts. The
observation adapter owns output validation; producers must eventually supply
complete semantic input hashes. Checked output bytes alone prove no freshness.
No artifact reads, hashing of live data, or receipt publication happens here.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType


STAGES = ("research", "ontology", "graph", "prepare", "run", "report")
STAGE_DEPENDENCIES = MappingProxyType({
    "research": (),
    "ontology": ("research",),
    "graph": ("research", "ontology"),
    "prepare": ("research", "graph"),
    "run": ("prepare",),
    "report": ("research", "graph", "run"),
})
STAGE_STATUSES = frozenset({"pending", "running", "completed", "failed", "cancelled"})
OUTPUT_STATUSES = frozenset({"unverified", "verified", "missing", "invalid"})


def _validate_choice(value: str, name: str, choices: frozenset[str]) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if value not in choices:
        raise ValueError(f"{name} must be one of {', '.join(sorted(choices))}")


def _freeze_inputs(value: Mapping[str, str] | None, name: str) -> Mapping[str, str] | None:
    """Copy before freezing: wrapping a caller's dictionary would still be mutable."""
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a mapping of strings to strings or None")
    copied = dict(value)
    if any(not isinstance(key, str) or not isinstance(digest, str) for key, digest in copied.items()):
        raise TypeError(f"{name} keys and input hashes must be strings")
    # Empty maps/identifiers/hashes retain their evidence meaning: the planner
    # rejects an incomplete modern record rather than silently treating it as legacy.
    return MappingProxyType(dict(sorted(copied.items())))


@dataclass(frozen=True, slots=True)
class StageObservation:
    """Snapshot of a stage, with exact semantic-input and generation-owner identity.

    Input maps contain semantic role -> opaque hash strings, including effective
    policies and upstream bindings relevant to this stage. Equality is exact;
    this contract cannot attest that the caller included every relevant input.
    ``recorded_inputs=None`` means legacy/unbound, whereas an empty map is an
    incomplete modern record. Unknown argument shapes are programming errors.

    Owners identify the generation being read, so a fork may legitimately retain
    its base owner's identity for shared upstream stages. Each stage compares its
    two owners; there is no requirement that all stages have the same owner.
    """

    status: str = "pending"
    current_inputs: Mapping[str, str] | None = None
    recorded_inputs: Mapping[str, str] | None = None
    output_status: str = "unverified"
    errors: tuple[str, ...] = ()
    owner_id: str | None = None
    recorded_owner_id: str | None = None

    def __post_init__(self) -> None:
        _validate_choice(self.status, "status", STAGE_STATUSES)
        _validate_choice(self.output_status, "output_status", OUTPUT_STATUSES)
        if not isinstance(self.errors, tuple) or any(not isinstance(error, str) for error in self.errors):
            raise TypeError("errors must be a tuple of strings")
        if any(not error.strip() for error in self.errors):
            raise ValueError("errors must contain nonempty reason codes")
        for name in ("owner_id", "recorded_owner_id"):
            owner = getattr(self, name)
            if owner is not None and not isinstance(owner, str):
                raise TypeError(f"{name} must be a string or None")
        for name in ("current_inputs", "recorded_inputs"):
            object.__setattr__(self, name, _freeze_inputs(getattr(self, name), name))
