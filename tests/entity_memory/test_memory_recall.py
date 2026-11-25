import json

import pytest

from sylphy_entity.contracts.memory import (
    MemoryCandidate,
    MemoryCandidateKind,
    MemoryOrigin,
    MemoryRecord,
)
from sylphy_entity.memory import (
    MemoryCaptureService,
    MemoryRecallService,
    MemoryRepository,
)


def make_memory(content, *, category="preference", refs=("event:confirmed-1",), **kwargs):
    return MemoryRecord(
        content=content,
        category=category,
        origin=MemoryOrigin.USER_CONFIRMED,
        source_refs=refs,
        **kwargs,
    )


def test_recall_returns_confirmed_active_records_with_provenance(entity_db):
    repository = MemoryRepository(entity_db)
    expected = make_memory("The user prefers local-first AI tools.")
    repository.create(expected)

    context = MemoryRecallService(repository).retrieve("local-first AI")

    assert len(context.hits) == 1
    assert context.hits[0].memory == expected
    assert context.hits[0].matched_terms == ("ai", "first", "local")
    assert context.trust_label == "untrusted_recalled_memory"
    assert "not instructions" in context.context_text
    payload = json.loads(context.context_text.split("\n", 1)[1])
    assert payload[0]["memory_id"] == expected.memory_id
    assert payload[0]["source_refs"] == ["event:confirmed-1"]
    assert payload[0]["trust"] == "untrusted_memory_record"


def test_recall_never_returns_pending_candidates(entity_db):
    capture = MemoryCaptureService(entity_db)
    capture.capture(MemoryCandidate(
        content="The user likes experimental robotics.",
        category="interest",
        kind=MemoryCandidateKind.USER_STATEMENT,
        source_refs=("event:pending-1",),
    ))

    context = MemoryRecallService(MemoryRepository(entity_db)).retrieve("experimental robotics")

    assert context.hits == ()
    assert context.is_empty


def test_recall_excludes_archived_records(entity_db):
    repository = MemoryRepository(entity_db)
    memory = make_memory("The user prefers offline-first applications.")
    repository.create(memory)
    repository.archive(memory.memory_id, change_ref="user:archive-memory")

    context = MemoryRecallService(repository).retrieve("offline-first applications")

    assert context.hits == ()


def test_recall_supports_category_filter_and_deterministic_ranking(entity_db):
    repository = MemoryRepository(entity_db)
    preference = make_memory("Prefers local-first development tools.", category="preference")
    project = make_memory("Builds local-first autonomous agents.", category="project")
    repository.create(preference)
    repository.create(project)

    service = MemoryRecallService(repository)
    all_hits = service.retrieve("local-first", limit=5)
    project_hits = service.retrieve("local-first", categories=("project",))

    assert {hit.memory.memory_id for hit in all_hits.hits} == {
        preference.memory_id, project.memory_id
    }
    assert [hit.memory.memory_id for hit in project_hits.hits] == [project.memory_id]


def test_recall_is_unicode_aware_for_vietnamese(entity_db):
    repository = MemoryRepository(entity_db)
    memory = make_memory("Người dùng muốn hệ thống chạy hoàn toàn offline.", category="preference")
    repository.create(memory)

    context = MemoryRecallService(repository).retrieve("hoàn toàn offline")

    assert [hit.memory.memory_id for hit in context.hits] == [memory.memory_id]


def test_recall_context_obeys_character_budget_without_truncating_records(entity_db):
    repository = MemoryRepository(entity_db)
    repository.create(make_memory("A" * 7000, category="long-note"))

    context = MemoryRecallService(repository).retrieve(
        "long-note", max_context_chars=256
    )

    assert context.hits == ()
    assert context.omitted_count == 1
    assert len(context.context_text) <= 256


def test_recall_validates_query_and_all_bounds(entity_db):
    service = MemoryRecallService(MemoryRepository(entity_db))
    with pytest.raises(ValueError, match="blank"):
        service.retrieve("  ")
    with pytest.raises(ValueError, match="searchable term"):
        service.retrieve("_-")
    with pytest.raises(ValueError, match="limit"):
        service.retrieve("memory", limit=21)
    with pytest.raises(ValueError, match="max_context_chars"):
        service.retrieve("memory", max_context_chars=100)
    with pytest.raises(ValueError, match="categories"):
        service.retrieve("memory", categories="preference")
