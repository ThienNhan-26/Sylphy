"""Bounded, deterministic operational drives for the Entity Brain."""

from __future__ import annotations

from sylphy_entity.contracts.decision import DriveInputs, DriveState

DRIVE_NAMES = (
    "curiosity",
    "continuity",
    "social_responsiveness",
    "self_maintenance",
    "resource_conservation",
)

_BASELINES = {
    "curiosity": 0.25,
    "continuity": 0.30,
    "social_responsiveness": 0.20,
    "self_maintenance": 0.10,
    "resource_conservation": 0.20,
}


def update_drives(
    drives: DriveState,
    *,
    dt: float,
    inputs: DriveInputs | None = None,
) -> DriveState:
    """Relax toward baselines, then apply bounded explicit pressures.

    The response is capped per tick so a long scheduler pause cannot produce an
    unbounded jump. dt is virtual elapsed time, never wall-clock time.
    """
    if dt < 0:
        dt = 0.0
    dt = min(float(dt), 300.0)
    source = inputs or DriveInputs()
    gain = min(0.25, dt * 0.05)
    relax = min(1.0, dt * 0.02)

    values = {
        name: getattr(drives, name)
        + (_BASELINES[name] - getattr(drives, name)) * relax
        for name in DRIVE_NAMES
    }
    values["curiosity"] += source.curiosity * gain
    values["continuity"] += source.continuity * gain
    values["social_responsiveness"] += source.social_responsiveness * gain
    values["self_maintenance"] += source.self_maintenance * gain
    values["resource_conservation"] += source.resource_pressure * gain
    bounded = {name: max(0.0, min(1.0, value)) for name, value in values.items()}
    return DriveState(**bounded)
