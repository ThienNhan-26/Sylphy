from pathlib import Path
from dataclasses import replace

import pytest

from sylphy_entity.contracts.decision import (
    Behaviour, BrainState, CognitionReceipt, DriveState, Goal, GoalStatus, NoveltyRecord,
)
from sylphy_entity.contracts.observation import Observation
from sylphy_entity.contracts.world import FactState, WorldFact, WorldSnapshot
from sylphy_entity.persistence.database import EntityDatabase
from sylphy_entity.persistence.runtime_store import RuntimeStore
from sylphy_entity.persistence.state_repository import StateRepository
from sylphy_entity.world.model import WorldModel


def _goal(status=GoalStatus.ACTIVE):
    return Goal(
        goal_id="goal-1", kind="inspect_file_change", subject="file:notes.txt",
        source="brain", priority=0.8, urgency=0.6,
        desired_outcome="inspect metadata", target="notes.txt", status=status,
        required_permissions=("OBSERVE_SANDBOX",), permission_scope="sandbox:/allowed",
        deadline_at_virtual=900.0,
    )


def test_brain_state_and_entity_identity_survive_database_reopen(tmp_path: Path):
    path = tmp_path / "entity.sqlite3"
    db1 = EntityDatabase(path)
    runtime1 = RuntimeStore(database=db1)
    runtime1.begin_runtime()
    repo1 = StateRepository(db1)
    entity_id = repo1.entity_id
    state = BrainState(
        behaviour=Behaviour.YIELDING, virtual_time=123.0,
        drives=DriveState(curiosity=0.8, continuity=0.7),
        goals=(replace(
            _goal(GoalStatus.SUSPENDED),
            resume_status=GoalStatus.ACTIVE,
            suspended_at_virtual=120.0,
        ),),
        novelty=(NoveltyRecord("file:notes.txt", 100.0, visit_count=3, depth=2),),
        yielded=True, yielded_since_virtual=120.0, transient_latches=("temporary-latch",),
        tick_count=77,
    )
    repo1.save_brain_state(state)
    runtime1.mark_clean_shutdown()
    runtime1.close()
    db1.close()

    db2 = EntityDatabase(path)
    runtime2 = RuntimeStore(database=db2)
    boot = runtime2.begin_runtime()
    repo2 = StateRepository(db2)
    restored = repo2.load_brain_state()
    try:
        assert repo2.entity_id == entity_id
        assert boot.boot_count == 2
        assert boot.previous_shutdown_clean is True
        assert restored is not None
        assert restored.virtual_time == 123.0
        assert restored.drives.curiosity == 0.8
        assert restored.novelty[0].visit_count == 3
        assert restored.goals[0].status is GoalStatus.ACTIVE
        assert restored.goals[0].deadline_at_virtual == 900.0
        assert restored.yielded is False
        assert restored.yielded_since_virtual is None
        assert restored.transient_latches == ()
        assert restored.behaviour is Behaviour.RECOVERING
    finally:
        runtime2.mark_clean_shutdown()
        runtime2.close()
        db2.close()


def test_world_snapshot_checksum_and_restore_stale_semantics(entity_db):
    repo = StateRepository(entity_db)
    observed = Observation(
        source_id="filesystem:sandbox", subject="file:notes.txt",
        kind="filesystem.file_created", payload={"relative_path": "notes.txt"},
        permission_scope="sandbox:/allowed",
    )
    model = WorldModel()
    assert model.apply(observed)
    snapshot = model.snapshot()
    repo.save_world_snapshot(snapshot, event_sequence=4)
    restored, sequence = repo.load_latest_world_snapshot()
    assert restored is not None and sequence == 4
    projected = WorldModel()
    projected.restore(restored)
    assert projected.get_fact("file:notes.txt").state is FactState.KNOWN
    assert projected.mark_all_stale()
    assert projected.get_fact("file:notes.txt").state is FactState.STALE


def test_world_snapshot_checksum_corruption_is_detected(entity_db):
    repo = StateRepository(entity_db)
    snapshot = WorldSnapshot(version=0)
    repo.save_world_snapshot(snapshot, event_sequence=0)
    with entity_db.transaction() as db:
        db.execute("UPDATE world_snapshots SET payload_json='{}'")
    with pytest.raises(RuntimeError, match="checksum"):
        repo.load_latest_world_snapshot()


def test_cognition_request_and_brain_review_survive_persistence(entity_db):
    repo = StateRepository(entity_db)
    receipt = CognitionReceipt(
        request_id="cog-persist-1", status="completed",
        accepted_as_evidence=True,
        review_reason="accepted as evidence-bounded context",
        source_refs=("obs-1",), summary="A file was observed.",
        findings=("file_event: metadata only",), proposals=(),
        model="local-test", elapsed_ms=17, processed_at_virtual=12.0,
    )
    state = BrainState(
        cognition_requested_refs=("obs-1",),
        cognition_receipts=(receipt,),
    )
    repo.save_brain_state(state)
    restored = repo.load_brain_state()
    assert restored is not None
    assert restored.cognition_requested_refs == ("obs-1",)
    assert restored.cognition_receipts == (receipt,)
