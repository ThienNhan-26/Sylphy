from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from sylphy_entity.contracts.observation import Observation
from sylphy_entity.contracts.world import FactState, WorldFact, WorldSnapshot


def test_observation_round_trips_as_json() -> None:
    observation = Observation(
        source_id="filesystem:sandbox",
        subject="file:notes/a.txt",
        kind="filesystem.file_created",
        payload={"relative_path": "notes/a.txt", "size_bytes": 7, "modified_ns": 42, "tags": ["a", "b"]},
        permission_scope="sandbox:C:/tmp/allowed",
    )
    restored = Observation.model_validate_json(observation.model_dump_json())
    assert restored == observation
    assert restored.observed_at.tzinfo is not None
    with pytest.raises(TypeError):
        observation.payload["size_bytes"] = 8  # type: ignore[index]
    with pytest.raises(AttributeError):
        observation.payload["tags"].append("c")  # type: ignore[union-attr]


def test_observation_rejects_blank_provenance_and_naive_timestamps() -> None:
    with pytest.raises(ValidationError):
        Observation(
            source_id=" ",
            subject="file:a.txt",
            kind="filesystem.file_created",
            permission_scope="sandbox:test",
        )
    with pytest.raises(ValidationError):
        Observation(
            source_id="sensor",
            subject="file:a.txt",
            kind="filesystem.file_created",
            permission_scope="sandbox:test",
            observed_at=datetime.now(),
        )


def test_world_snapshot_is_immutable() -> None:
    fact = WorldFact(
        subject="file:a.txt",
        state=FactState.KNOWN,
        value={"size_bytes": 1},
        last_observation_id="obs-1",
        last_observed_at=datetime.now(timezone.utc),
    )
    snapshot = WorldSnapshot(version=1, facts=(fact,))
    with pytest.raises(ValidationError):
        snapshot.version = 2  # type: ignore[misc]
    with pytest.raises(TypeError):
        fact.value["size_bytes"] = 9  # type: ignore[index]


def test_world_snapshot_carries_runtime_context_immutably() -> None:
    snapshot = WorldSnapshot(
        version=1,
        user_interaction_active=True,
        running_work=("worker-a",),
        resource_pressure=0.8,
        externally_owned_resources=("filesystem:sandbox:notes.txt",),
        active_goal_ids=("goal-1",),
        recent_events=("event-1",),
    )
    assert snapshot.user_interaction_active
    assert snapshot.running_work == ("worker-a",)
    assert snapshot.resource_pressure == 0.8
    assert snapshot.externally_owned_resources
    with pytest.raises(ValidationError):
        WorldSnapshot(version=1, resource_pressure=1.5)
