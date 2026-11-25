"""In-memory authoritative World Model for Stage 2.

Durable event history and replay are deliberately deferred to Stage 5.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from typing import Any

from sylphy_entity.contracts.observation import Observation
from sylphy_entity.contracts.world import FactState, WorldFact, WorldSnapshot


class WorldModel:
    def __init__(self, *, recent_limit: int = 100, dedupe_limit: int = 4096) -> None:
        if recent_limit < 1 or dedupe_limit < 1:
            raise ValueError("history limits must be positive")
        self._facts: dict[str, WorldFact] = {}
        self._recent: deque[str] = deque(maxlen=recent_limit)
        self._recent_events: deque[str] = deque(maxlen=recent_limit)
        self._seen: deque[str] = deque(maxlen=dedupe_limit)
        self._seen_set: set[str] = set()
        self._seen_events: deque[str] = deque(maxlen=dedupe_limit)
        self._seen_event_set: set[str] = set()
        self._version = 0

    @property
    def version(self) -> int:
        return self._version

    @property
    def fact_count(self) -> int:
        return len(self._facts)

    def get_fact(self, subject: str) -> WorldFact | None:
        return self._facts.get(subject)

    def restore(self, snapshot: WorldSnapshot) -> None:
        """Restore a persisted snapshot without treating it as freshly observed."""
        self._facts = {fact.subject: fact for fact in snapshot.facts}
        self._version = snapshot.version
        self._recent = deque(snapshot.recent_observation_ids, maxlen=self._recent.maxlen)
        self._seen = deque(snapshot.recent_observation_ids, maxlen=self._seen.maxlen)
        self._seen_set = set(snapshot.recent_observation_ids)
        self._recent_events = deque(snapshot.recent_events, maxlen=self._recent_events.maxlen)
        self._seen_events = deque(snapshot.recent_events, maxlen=self._seen_events.maxlen)
        self._seen_event_set = set(snapshot.recent_events)

    def record_event(self, event_id: str) -> bool:
        """Project a durable Entity Event reference without copying its payload.

        Event payloads remain in EventStore. The World snapshot exposes only a
        bounded, deduplicated sequence of event IDs for inspection/context.
        """
        if not isinstance(event_id, str) or not event_id.strip():
            raise ValueError("event_id must not be blank")
        if event_id in self._seen_event_set:
            return False
        if len(self._seen_events) == self._seen_events.maxlen:
            oldest = self._seen_events[0]
            self._seen_event_set.discard(oldest)
        self._seen_events.append(event_id)
        self._seen_event_set.add(event_id)
        self._recent_events.append(event_id)
        self._version += 1
        return True

    def mark_all_stale(self, *, reason: str = "re-observation_required_after_restart") -> bool:
        """Keep old evidence but prevent pre-restart facts being treated as current."""
        changed = False
        now = datetime.now(timezone.utc)
        for subject, fact in tuple(self._facts.items()):
            if fact.state is FactState.KNOWN:
                self._facts[subject] = fact.model_copy(
                    update={"state": FactState.STALE, "updated_at": now, "reason": reason}
                )
                changed = True
        if changed:
            self._version += 1
        return changed

    def apply(self, observation: Observation) -> bool:
        """Project one observation; return True only when world state changed."""
        if observation.observation_id in self._seen_set:
            return False
        self._remember(observation.observation_id)
        self._recent.append(observation.observation_id)

        now = datetime.now(timezone.utc)
        kind = observation.kind
        subject = observation.subject
        payload = dict(observation.payload)
        # Preserve observation semantics in the World Fact. The Brain's file
        # policy must distinguish discovery/change/restoration from arbitrary
        # payloads; metadata alone is not an executable instruction.
        if kind.startswith("filesystem.file_"):
            payload.setdefault("kind", kind)

        if kind == "filesystem.file_renamed":
            old_relative = payload.get("old_relative_path")
            old_changed = False
            if isinstance(old_relative, str) and old_relative:
                old_subject = f"file:{old_relative}"
                old_changed = self._set_fact(
                    old_subject,
                    FactState.UNKNOWN,
                    None,
                    observation,
                    now,
                    "renamed",
                )
            new_payload = dict(payload)
            new_payload.pop("old_relative_path", None)
            return self._set_fact(
                subject,
                FactState.KNOWN,
                new_payload,
                observation,
                now,
                None,
            ) or old_changed

        if kind == "filesystem.file_deleted":
            return self._set_fact(
                subject,
                FactState.UNKNOWN,
                None,
                observation,
                now,
                "deleted",
            )

        if kind == "filesystem.file_unavailable":
            existing = self._facts.get(subject)
            retained = dict(existing.value) if existing and existing.value else None
            return self._set_fact(
                subject,
                FactState.STALE,
                retained,
                observation,
                now,
                "inaccessible_or_disappeared_during_observation",
            )

        # Only explicit filesystem facts are projected at this stage. Other
        # event types remain events, not guessed world state.
        if kind in {
            "filesystem.file_discovered",
            "filesystem.file_created",
            "filesystem.file_modified",
            "filesystem.file_restored",
        }:
            return self._set_fact(
                subject,
                FactState.KNOWN,
                payload,
                observation,
                now,
                None,
            )
        return False

    def snapshot(self) -> WorldSnapshot:
        facts = tuple(self._facts[key] for key in sorted(self._facts))
        return WorldSnapshot(
            version=self._version,
            captured_at=datetime.now(timezone.utc),
            facts=facts,
            recent_observation_ids=tuple(self._recent),
            recent_events=tuple(self._recent_events),
        )

    def _remember(self, observation_id: str) -> None:
        if len(self._seen) == self._seen.maxlen:
            oldest = self._seen[0]
            self._seen_set.discard(oldest)
        self._seen.append(observation_id)
        self._seen_set.add(observation_id)

    def _set_fact(
        self,
        subject: str,
        state: FactState,
        value: dict[str, Any] | None,
        observation: Observation,
        now: datetime,
        reason: str | None,
    ) -> bool:
        previous = self._facts.get(subject)
        if (
            previous is not None
            and previous.state == state
            and previous.value == value
            and previous.reason == reason
            and previous.source_id == observation.source_id
            and previous.confidence == observation.confidence
            and previous.verification == observation.verification
            and previous.permission_scope == observation.permission_scope
        ):
            # Refresh provenance without incrementing the semantic World
            # version: a repeated equal observation is evidence freshness,
            # not a changed fact that should wake the Brain as new novelty.
            self._facts[subject] = previous.model_copy(
                update={
                    "last_observation_id": observation.observation_id,
                    "last_observed_at": observation.observed_at,
                    "last_sequence": observation.sequence,
                    "updated_at": now,
                }
            )
            return False
        self._facts[subject] = WorldFact(
            subject=subject,
            state=state,
            value=value,
            last_observation_id=observation.observation_id,
            last_observed_at=observation.observed_at,
            source_id=observation.source_id,
            confidence=observation.confidence,
            verification=observation.verification,
            permission_scope=observation.permission_scope,
            last_sequence=observation.sequence,
            updated_at=now,
            reason=reason,
        )
        self._version += 1
        return True
