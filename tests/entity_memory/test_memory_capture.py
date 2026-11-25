import pytest

from sylphy_entity.contracts.memory import (
    MemoryCandidate,
    MemoryCandidateKind,
    MemoryCandidateStatus,
    MemoryOrigin,
)
from sylphy_entity.memory import MemoryCaptureService, MemoryRepository


def candidate(kind=MemoryCandidateKind.USER_STATEMENT, **overrides):
    values = {
        "content": "The user prefers local-first tools.",
        "category": "preference",
        "kind": kind,
        "source_refs": ("event:source-1",),
    }
    values.update(overrides)
    return MemoryCandidate(**values)


def test_confirmed_candidate_contract_requires_decision_and_result_refs():
    with pytest.raises(ValueError, match="decision_ref"):
        candidate(status=MemoryCandidateStatus.CONFIRMED, resulting_memory_id="memory-1")


def test_capture_stages_pending_candidate_without_creating_memory(entity_db):
    service = MemoryCaptureService(entity_db)
    proposed = candidate()
    assert service.capture(proposed) == proposed
    assert service.get_candidate(proposed.candidate_id).status is MemoryCandidateStatus.PENDING
    assert MemoryRepository(entity_db).list_active() == ()


def test_capture_is_idempotent_and_rejects_candidate_id_collision(entity_db):
    service = MemoryCaptureService(entity_db)
    proposed = candidate()
    assert service.capture(proposed) == proposed
    assert service.capture(proposed) == proposed
    with pytest.raises(ValueError, match="reused"):
        service.capture(candidate(candidate_id=proposed.candidate_id, content="Changed"))


def test_user_confirmation_promotes_only_user_statement_with_confirmation_provenance(entity_db):
    service = MemoryCaptureService(entity_db)
    proposed = candidate()
    service.capture(proposed)
    memory = service.confirm_user_statement(
        proposed.candidate_id, user_confirmation_ref="confirmation:user-42"
    )
    assert memory.origin is MemoryOrigin.USER_CONFIRMED
    assert memory.source_refs == ("event:source-1", "confirmation:user-42")
    assert service.get_candidate(proposed.candidate_id).status is MemoryCandidateStatus.CONFIRMED
    assert MemoryRepository(entity_db).get(memory.memory_id) == memory
    assert service.confirm_user_statement(
        proposed.candidate_id, user_confirmation_ref="confirmation:user-42"
    ) == memory


def test_cognition_proposal_requires_brain_review_and_user_confirmation(entity_db):
    service = MemoryCaptureService(entity_db)
    proposed = candidate(MemoryCandidateKind.COGNITION_PROPOSAL)
    service.capture(proposed)
    with pytest.raises(TypeError):
        service.confirm_cognition_proposal(proposed.candidate_id, brain_review_ref="review:1")
    memory = service.confirm_cognition_proposal(
        proposed.candidate_id, brain_review_ref="brain-review:1",
        user_confirmation_ref="confirmation:user-1",
    )
    assert memory.origin is MemoryOrigin.COGNITION_CONFIRMED
    assert "brain-review:1" in memory.source_refs
    assert "confirmation:user-1" in memory.source_refs


def test_external_import_requires_verification_and_user_confirmation(entity_db):
    service = MemoryCaptureService(entity_db)
    proposed = candidate(MemoryCandidateKind.EXTERNAL_IMPORT)
    service.capture(proposed)
    memory = service.confirm_verified_import(
        proposed.candidate_id, verification_ref="verification:sha256-abc",
        user_confirmation_ref="confirmation:user-2",
    )
    assert memory.origin is MemoryOrigin.IMPORTED_VERIFIED
    assert "verification:sha256-abc" in memory.source_refs


def test_confirmation_path_cannot_be_used_for_wrong_candidate_kind(entity_db):
    service = MemoryCaptureService(entity_db)
    proposed = candidate(MemoryCandidateKind.COGNITION_PROPOSAL)
    service.capture(proposed)
    with pytest.raises(ValueError, match="cannot use this confirmation path"):
        service.confirm_user_statement(
            proposed.candidate_id, user_confirmation_ref="confirmation:user-1"
        )
    assert service.get_candidate(proposed.candidate_id).status is MemoryCandidateStatus.PENDING
    assert MemoryRepository(entity_db).list_active() == ()


def test_rejected_candidate_never_becomes_memory_and_cannot_be_confirmed(entity_db):
    service = MemoryCaptureService(entity_db)
    proposed = candidate()
    service.capture(proposed)
    rejected = service.reject(proposed.candidate_id, decision_ref="decision:reject-1")
    assert rejected.status is MemoryCandidateStatus.REJECTED
    assert service.reject(proposed.candidate_id, decision_ref="ignored") == rejected
    with pytest.raises(ValueError, match="only pending"):
        service.confirm_user_statement(
            proposed.candidate_id, user_confirmation_ref="confirmation:user-1"
        )
    assert MemoryRepository(entity_db).list_active() == ()


def test_capture_requires_unique_provenance_and_rejects_blank_decision_ref(entity_db):
    with pytest.raises(ValueError, match="unique"):
        candidate(source_refs=("same", "same"))
    service = MemoryCaptureService(entity_db)
    proposed = candidate()
    service.capture(proposed)
    with pytest.raises(ValueError, match="blank"):
        service.reject(proposed.candidate_id, decision_ref=" ")


def test_v3_database_migrates_additively_to_v4_candidates(tmp_path):
    import sqlite3
    from sylphy_entity.persistence.database import EntityDatabase

    path = tmp_path / "entity-v3.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA user_version=3")
    connection.commit()
    connection.close()
    database = EntityDatabase(path)
    database.open()
    try:
        with database.read() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            table = db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='entity_memory_candidates'"
            ).fetchone()
        assert version == 5
        assert table is not None
    finally:
        database.close()
