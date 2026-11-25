from datetime import datetime, timezone

from sylphy_entity.contracts.observation import Observation
from sylphy_entity.contracts.world import FactState
from sylphy_entity.world.model import WorldModel


def obs(kind: str, *, subject: str = "file:a.txt", payload: dict | None = None, oid: str = "1") -> Observation:
    return Observation(
        observation_id=oid,
        source_id="filesystem:test",
        subject=subject,
        kind=kind,
        payload=payload or {"relative_path": "a.txt", "size_bytes": 10, "modified_ns": 1},
        observed_at=datetime.now(timezone.utc),
        permission_scope="sandbox:test",
    )


def test_create_update_delete_progresses_known_to_unknown() -> None:
    model = WorldModel()
    assert model.apply(obs("filesystem.file_created")) is True
    fact = model.get_fact("file:a.txt")
    assert fact is not None and fact.state is FactState.KNOWN

    assert model.apply(obs("filesystem.file_modified", payload={"size_bytes": 12}, oid="2")) is True
    assert model.get_fact("file:a.txt").value == {"size_bytes": 12, "kind": "filesystem.file_modified"}  # type: ignore[union-attr]

    assert model.apply(obs("filesystem.file_deleted", payload={}, oid="3")) is True
    deleted = model.get_fact("file:a.txt")
    assert deleted is not None
    assert deleted.state is FactState.UNKNOWN
    assert deleted.reason == "deleted"
    assert deleted.value is None


def test_inaccessible_fact_becomes_stale_not_absent() -> None:
    model = WorldModel()
    model.apply(obs("filesystem.file_discovered", oid="1"))
    assert model.apply(
        obs("filesystem.file_unavailable", payload={}, oid="2")
    ) is True
    fact = model.get_fact("file:a.txt")
    assert fact is not None
    assert fact.state is FactState.STALE
    assert fact.value == {"relative_path": "a.txt", "size_bytes": 10, "modified_ns": 1, "kind": "filesystem.file_discovered"}


def test_duplicate_ids_and_duplicate_state_do_not_increase_world_version() -> None:
    model = WorldModel()
    first = obs("filesystem.file_created", oid="same")
    assert model.apply(first)
    version = model.version
    assert model.apply(first) is False
    equivalent = obs("filesystem.file_created", oid="different")
    assert model.apply(equivalent) is False
    assert model.version == version
    assert model.get_fact("file:a.txt").last_observation_id == "different"  # type: ignore[union-attr]


def test_rename_marks_old_path_unknown_and_new_path_known() -> None:
    model = WorldModel()
    model.apply(obs("filesystem.file_created", oid="1"))
    renamed = obs(
        "filesystem.file_renamed",
        subject="file:b.txt",
        payload={"relative_path": "b.txt", "size_bytes": 10, "modified_ns": 1, "old_relative_path": "a.txt"},
        oid="2",
    )
    assert model.apply(renamed)
    assert model.get_fact("file:a.txt").state is FactState.UNKNOWN  # type: ignore[union-attr]
    assert model.get_fact("file:b.txt").state is FactState.KNOWN  # type: ignore[union-attr]


def test_world_fact_preserves_observation_evidence_and_scope() -> None:
    observation = Observation(
        observation_id="evidence-1",
        source_id="filesystem:sandbox",
        subject="file:evidence.txt",
        kind="filesystem.file_created",
        payload={"relative_path": "evidence.txt"},
        sequence=17,
        confidence=0.92,
        verification="confirmed",
        permission_scope="sandbox:C:/authorized",
    )
    model = WorldModel()
    assert model.apply(observation)
    fact = model.get_fact("file:evidence.txt")
    assert fact is not None
    assert fact.source_id == "filesystem:sandbox"
    assert fact.confidence == 0.92
    assert fact.verification == "confirmed"
    assert fact.permission_scope == "sandbox:C:/authorized"
    assert fact.last_sequence == 17


def test_repeated_equivalent_observation_refreshes_provenance_without_world_wakeup() -> None:
    model = WorldModel()
    first = obs("filesystem.file_created", oid="first")
    assert model.apply(first)
    version = model.version
    second = obs("filesystem.file_created", oid="second")
    assert model.apply(second) is False
    fact = model.get_fact("file:a.txt")
    assert fact is not None
    assert fact.last_observation_id == "second"
    assert model.version == version


def test_record_event_is_bounded_deduplicated_and_restored() -> None:
    from sylphy_entity.contracts.world import WorldSnapshot

    model = WorldModel(recent_limit=2, dedupe_limit=4)
    assert model.record_event("event-1") is True
    version = model.version
    assert model.record_event("event-1") is False
    assert model.version == version
    assert model.record_event("event-2") is True
    assert model.record_event("event-3") is True
    snapshot = model.snapshot()
    assert snapshot.recent_events == ("event-2", "event-3")

    restored = WorldModel(recent_limit=2, dedupe_limit=4)
    restored.restore(snapshot)
    assert restored.snapshot().recent_events == ("event-2", "event-3")
    assert restored.record_event("event-3") is False
    assert restored.record_event("event-4") is True


def test_record_event_rejects_blank_id() -> None:
    import pytest

    with pytest.raises(ValueError, match="event_id must not be blank"):
        WorldModel().record_event(" ")
