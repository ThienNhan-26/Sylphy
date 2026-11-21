"""Small deterministic decision contract for user-facing interaction events."""

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum


class InteractionAction(str, Enum):
    SPEAK = "SPEAK"
    EXPLORE = "EXPLORE"
    WAIT = "WAIT"
    REQUEST_PERMISSION = "REQUEST_PERMISSION"
    SILENCE = "SILENCE"


class InteractionReason(str, Enum):
    ELIGIBLE_TO_SHARE = "eligible_to_share"
    NEEDS_MORE_CONTEXT = "needs_more_context"
    PERMISSION_REQUIRED = "permission_required"
    PERMISSION_DENIED = "permission_denied"
    FEATURE_DISABLED = "feature_disabled"
    DUPLICATE_EVENT = "duplicate_event"
    DISMISSED_BY_USER = "dismissed_by_user"
    COOLDOWN_ACTIVE = "cooldown_active"
    EXPIRED_EVENT = "expired_event"
    QUEUE_FULL = "queue_full"
    INSUFFICIENT_CONFIDENCE = "insufficient_confidence"
    NOT_RELEVANT_NOW = "not_relevant_now"
    RESOURCE_UNAVAILABLE = "resource_unavailable"
    INVALID_SOURCE = "invalid_source"


@dataclass(frozen=True)
class InteractionEvent:
    event_id: str
    source: str
    occurred_at: datetime
    dedup_key: str
    permission_scope: str
    payload: str
    expires_at: datetime | None = None

    def __post_init__(self) -> None:
        for name in ("event_id", "source", "dedup_key", "permission_scope", "payload"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        for name in ("occurred_at", "expires_at"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, datetime) or value.tzinfo is None):
                raise ValueError(f"{name} must be timezone-aware")


@dataclass(frozen=True)
class PolicyConditions:
    """Application-verified conditions; no value is inferred from model output."""

    feature_enabled: bool = True
    permission_granted: bool = True
    permission_denied: bool = False
    dismissed: bool = False
    duplicate: bool = False
    expired: bool = False
    cooldown_active: bool = False
    queue_available: bool = True
    resource_available: bool = True
    grounded: bool = True
    relevant: bool = True
    needs_context: bool = False
    request_permission: bool = False
    can_wait: bool = False
    requested_action: InteractionAction = InteractionAction.SPEAK
    not_before: datetime | None = None


@dataclass(frozen=True)
class PolicyDecision:
    action: InteractionAction
    reason_code: InteractionReason
    source_event_id: str
    dedup_key: str
    message: str | None = None
    required_permission: str | None = None
    not_before: datetime | None = None
    expires_at: datetime | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.action, InteractionAction) or not isinstance(self.reason_code, InteractionReason):
            raise ValueError("Interaction decision requires supported action and reason codes")
        if not self.source_event_id.strip() or not self.dedup_key.strip():
            raise ValueError("Interaction decision must preserve event provenance")
        if self.action is InteractionAction.SPEAK and not (self.message and self.message.strip()):
            raise ValueError("SPEAK decisions require a message")
        if self.action is not InteractionAction.SPEAK and self.message is not None:
            raise ValueError("Only SPEAK decisions may contain a message")
        if self.action is InteractionAction.REQUEST_PERMISSION and not self.required_permission:
            raise ValueError("REQUEST_PERMISSION decisions require a permission scope")
        if self.action is not InteractionAction.REQUEST_PERMISSION and self.required_permission is not None:
            raise ValueError("Only REQUEST_PERMISSION decisions may name a permission")
        for value in (self.not_before, self.expires_at):
            if value is not None and (not isinstance(value, datetime) or value.tzinfo is None):
                raise ValueError("Interaction decision times must be timezone-aware")


class InteractionPolicy:
    """Apply hard, deterministic gates before selecting an interaction outcome."""

    def decide(self, event: InteractionEvent, conditions: PolicyConditions) -> PolicyDecision:
        now = datetime.now(timezone.utc)

        def decision(action: InteractionAction, reason: InteractionReason, **kwargs) -> PolicyDecision:
            return PolicyDecision(action, reason, event.event_id, event.dedup_key, **kwargs)

        if event.source not in {"conversation", "exploration", "presence", "investigation"}:
            return decision(InteractionAction.SILENCE, InteractionReason.INVALID_SOURCE)
        if not isinstance(conditions.requested_action, InteractionAction):
            return decision(InteractionAction.SILENCE, InteractionReason.INVALID_SOURCE)
        if conditions.permission_denied:
            return decision(InteractionAction.SILENCE, InteractionReason.PERMISSION_DENIED)
        if conditions.dismissed:
            return decision(InteractionAction.SILENCE, InteractionReason.DISMISSED_BY_USER)
        if conditions.expired or (event.expires_at is not None and event.expires_at <= now):
            return decision(InteractionAction.SILENCE, InteractionReason.EXPIRED_EVENT)
        if not conditions.feature_enabled:
            return decision(InteractionAction.SILENCE, InteractionReason.FEATURE_DISABLED)
        if conditions.duplicate:
            return decision(InteractionAction.SILENCE, InteractionReason.DUPLICATE_EVENT)
        if conditions.cooldown_active:
            if conditions.can_wait:
                return decision(InteractionAction.WAIT, InteractionReason.COOLDOWN_ACTIVE,
                                not_before=conditions.not_before, expires_at=event.expires_at)
            return decision(InteractionAction.SILENCE, InteractionReason.COOLDOWN_ACTIVE)
        if not conditions.queue_available:
            return decision(InteractionAction.SILENCE, InteractionReason.QUEUE_FULL)
        if not conditions.resource_available:
            if conditions.can_wait:
                return decision(InteractionAction.WAIT, InteractionReason.RESOURCE_UNAVAILABLE,
                                not_before=conditions.not_before, expires_at=event.expires_at)
            return decision(InteractionAction.SILENCE, InteractionReason.RESOURCE_UNAVAILABLE)
        if conditions.request_permission and not conditions.permission_granted:
            return decision(InteractionAction.REQUEST_PERMISSION, InteractionReason.PERMISSION_REQUIRED,
                            required_permission=event.permission_scope)
        if not conditions.grounded:
            return decision(InteractionAction.SILENCE, InteractionReason.INSUFFICIENT_CONFIDENCE)
        if not conditions.relevant:
            return decision(InteractionAction.SILENCE, InteractionReason.NOT_RELEVANT_NOW)
        if conditions.needs_context:
            return decision(InteractionAction.EXPLORE, InteractionReason.NEEDS_MORE_CONTEXT,
                            expires_at=event.expires_at)
        if conditions.requested_action is InteractionAction.WAIT:
            if conditions.can_wait:
                return decision(InteractionAction.WAIT, InteractionReason.NOT_RELEVANT_NOW,
                                not_before=conditions.not_before, expires_at=event.expires_at)
            return decision(InteractionAction.SILENCE, InteractionReason.NOT_RELEVANT_NOW)
        if conditions.requested_action is InteractionAction.REQUEST_PERMISSION:
            if conditions.permission_granted:
                return decision(InteractionAction.SILENCE, InteractionReason.NOT_RELEVANT_NOW)
            return decision(InteractionAction.REQUEST_PERMISSION, InteractionReason.PERMISSION_REQUIRED,
                            required_permission=event.permission_scope)
        if conditions.requested_action is InteractionAction.EXPLORE:
            return decision(InteractionAction.EXPLORE, InteractionReason.NEEDS_MORE_CONTEXT,
                            expires_at=event.expires_at)
        if conditions.requested_action is InteractionAction.SILENCE:
            return decision(InteractionAction.SILENCE, InteractionReason.NOT_RELEVANT_NOW)
        return decision(InteractionAction.SPEAK, InteractionReason.ELIGIBLE_TO_SHARE,
                        message=event.payload, expires_at=event.expires_at)
