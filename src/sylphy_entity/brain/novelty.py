"""Domain-specific, bounded novelty memory with recency/frequency/depth."""

from __future__ import annotations

import math

from sylphy_entity.contracts.decision import NoveltyRecord


def novelty_score(
    subject: str,
    history: tuple[NoveltyRecord, ...],
    now: float,
    *,
    relevance: float = 1.0,
    staleness: float = 0.0,
    half_life_seconds: float = 3600.0,
) -> float:
    """Return a deterministic [0, 1] score.

    Recency controls recovery of novelty; repeated visits and deeper prior
    inspection reduce repeated attention. Relevance scales the result and
    staleness suppresses decisions based on aging observations.
    """
    if half_life_seconds <= 0:
        raise ValueError("half_life_seconds must be positive")
    if not math.isfinite(now) or now < 0:
        raise ValueError("now must be finite and non-negative")
    relevance = max(0.0, min(1.0, float(relevance)))
    staleness = max(0.0, min(1.0, float(staleness)))
    record = next((item for item in reversed(history) if item.subject == subject), None)
    if record is None:
        recency = 1.0
        frequency_factor = 1.0
        depth_factor = 1.0
    else:
        age = max(0.0, now - record.last_seen_virtual)
        recency = 1.0 - math.exp(-math.log(2.0) * age / half_life_seconds)
        frequency_factor = 1.0 / (1.0 + 0.15 * math.log2(record.visit_count))
        depth_factor = 1.0 / (1.0 + 0.10 * record.depth)
    return max(0.0, min(1.0, recency * frequency_factor * depth_factor * relevance * (1.0 - staleness)))


def record_visit(
    history: tuple[NoveltyRecord, ...],
    subject: str,
    now: float,
    *,
    depth_increment: int = 1,
    max_entries: int = 256,
) -> tuple[NoveltyRecord, ...]:
    if max_entries < 1:
        raise ValueError("max_entries must be positive")
    if depth_increment < 0:
        raise ValueError("depth_increment must be non-negative")
    previous = next((item for item in history if item.subject == subject), None)
    updated = [item for item in history if item.subject != subject]
    updated.append(
        NoveltyRecord(
            subject=subject,
            last_seen_virtual=now,
            visit_count=(previous.visit_count + 1) if previous else 1,
            depth=(previous.depth + depth_increment) if previous else depth_increment,
        )
    )
    updated.sort(key=lambda item: item.last_seen_virtual)
    return tuple(updated[-max_entries:])
