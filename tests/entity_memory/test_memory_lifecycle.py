from datetime import timezone

import pytest

from sylphy_entity.contracts.memory import MemoryOrigin, MemoryRecord, MemoryStatus
from sylphy_entity.memory import MemoryRepository


def _memory(**changes):
    values = dict(content="User prefers local-first software.", category="preference",
                  origin=MemoryOrigin.USER_CONFIRMED, source_refs=("event:user:1",))
    values.update(changes)
    return MemoryRecord(**values)


def test_correction_preserves_prior_snapshot_and_provenance(entity_db):
    repo = MemoryRepository(entity_db)
    original = _memory()
    repo.create(original)
    corrected = repo.correct(original.memory_id, content="User prefers offline-first software.",
                              change_ref="user-confirmation:correction-1")
    history = repo.history(original.memory_id)
    assert corrected.content == "User prefers offline-first software."
    assert len(history) == 1
    assert history[0].action == "corrected"
    assert history[0].content == original.content
    assert history[0].source_refs == original.source_refs
    assert history[0].change_refs == ("user-confirmation:correction-1",)


def test_noop_correction_does_not_create_revision(entity_db):
    repo = MemoryRepository(entity_db)
    memory = _memory()
    repo.create(memory)
    assert repo.correct(memory.memory_id, content=memory.content, change_ref="user:noop") == memory
    assert repo.history(memory.memory_id) == ()


def test_correction_rejects_duplicate_without_mutating_or_history(entity_db):
    repo = MemoryRepository(entity_db)
    first, second = _memory(), _memory(content="Another preference.")
    repo.create(first); repo.create(second)
    with pytest.raises(ValueError, match="exact active"):
        repo.correct(second.memory_id, content=first.content, change_ref="user:dup")
    assert repo.get(second.memory_id) == second
    assert repo.history(second.memory_id) == ()


def test_archive_restore_are_audited_and_idempotent(entity_db):
    repo = MemoryRepository(entity_db)
    memory = _memory()
    repo.create(memory)
    assert repo.archive_with_history(memory.memory_id, change_ref="user:archive") is True
    assert repo.archive_with_history(memory.memory_id, change_ref="user:archive-again") is False
    assert repo.get(memory.memory_id).status is MemoryStatus.ARCHIVED
    assert repo.restore(memory.memory_id, change_ref="user:restore") is True
    assert repo.get(memory.memory_id).status is MemoryStatus.ACTIVE
    assert [item.action for item in repo.history(memory.memory_id)] == ["archived", "restored"]


def test_lifecycle_requires_explicit_change_reference(entity_db):
    repo = MemoryRepository(entity_db)
    memory = _memory(); repo.create(memory)
    with pytest.raises(ValueError, match="change_ref"):
        repo.archive_with_history(memory.memory_id, change_ref=" ")
    with pytest.raises(ValueError, match="change_ref"):
        repo.restore(memory.memory_id, change_ref=" ")


def test_revision_history_is_bounded(entity_db):
    repo = MemoryRepository(entity_db)
    with pytest.raises(ValueError, match="limit"):
        repo.history("missing", limit=0)


def test_restore_rejects_exact_active_duplicate(entity_db):
    repo = MemoryRepository(entity_db)
    archived = _memory(memory_id="archived-one")
    active = _memory(memory_id="active-two")
    repo.create(archived); repo.create(active)
    repo.archive(archived.memory_id, change_ref="user:archive")
    with pytest.raises(ValueError, match="exact active"):
        repo.restore(archived.memory_id, change_ref="user:restore")
    assert repo.get(archived.memory_id).status is MemoryStatus.ARCHIVED
    assert [item.action for item in repo.history(archived.memory_id)] == ["archived"]
