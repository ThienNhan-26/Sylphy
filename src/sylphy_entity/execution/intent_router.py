"""Validate Brain proposals and map them to the bounded Stage-4 Intent API."""

from __future__ import annotations

import time
from pathlib import PurePosixPath, PureWindowsPath

from sylphy_entity.contracts.decision import IntentProposal
from sylphy_entity.contracts.execution import (
    CancellationPolicy,
    Intent,
    IntentOrigin,
    PermissionClass,
    ResourceMode,
    ResourceRequirement,
)


class IntentRejected(ValueError):
    pass


_CAPABILITIES: dict[str, PermissionClass] = {
    "inspect_file_metadata": PermissionClass.OBSERVE_SANDBOX,
    "read_sandbox_text": PermissionClass.READ_SANDBOX,
}


class IntentRouter:
    def __init__(self, *, timeout_seconds: float = 15.0, deadline_seconds: float = 60.0) -> None:
        if not 0.0 < timeout_seconds <= 300.0:
            raise ValueError("timeout_seconds must be in (0, 300]")
        if deadline_seconds < 0.0:
            raise ValueError("deadline_seconds must be non-negative")
        self._timeout = timeout_seconds
        self._deadline = deadline_seconds

    def route(
        self,
        proposal: IntentProposal,
        *,
        origin: IntentOrigin = IntentOrigin.AUTONOMOUS,
    ) -> Intent:
        required_permission = _CAPABILITIES.get(proposal.capability)
        if required_permission is None:
            raise IntentRejected(f"capability is not registered: {proposal.capability}")
        try:
            declared_permission = PermissionClass(proposal.permission_class)
        except ValueError as exc:
            raise IntentRejected("unknown permission class") from exc
        if declared_permission is not required_permission:
            raise IntentRejected("proposal permission does not match capability policy")
        if not (proposal.permission_scope.startswith("sandbox:") or proposal.permission_scope == "simulated:sandbox"):
            raise IntentRejected("proposal is outside a recognized sandbox scope")
        relative = self._safe_relative_path(proposal.target)
        if relative is None:
            raise IntentRejected("target must be a safe relative sandbox path")
        argument_items = tuple(proposal.arguments)
        argument_names = [item[0] for item in argument_items]
        if len(set(argument_names)) != len(argument_names):
            raise IntentRejected("proposal contains duplicate argument names")
        if set(argument_names) != {"relative_path"}:
            raise IntentRejected("proposal arguments must contain only relative_path")
        arguments = dict(argument_items)
        argument_path = arguments["relative_path"]
        if not isinstance(argument_path, str) or self._safe_relative_path(argument_path) != relative:
            raise IntentRejected("relative_path argument does not match target")
        expected_resource = f"filesystem:sandbox:{relative}"
        if tuple(proposal.required_resources) != (expected_resource,):
            raise IntentRejected("proposal resource requirement is not the expected path-scoped sandbox resource")

        now = time.monotonic()
        return Intent(
            intent_id=proposal.intent_id,
            goal_id=proposal.goal_id,
            capability=proposal.capability,
            target=relative,
            arguments=(("relative_path", relative),),
            reason=proposal.reason,
            preconditions=("target_exists", "target_is_regular_file"),
            permission_class=required_permission,
            permission_scope=proposal.permission_scope,
            resources=(
                ResourceRequirement(
                    resource_key=f"filesystem:{proposal.permission_scope}:{relative}",
                    mode=ResourceMode.SHARED,
                ),
            ),
            priority=proposal.priority,
            expected_outcome=proposal.expected_outcome,
            idempotency_key=proposal.intent_id,
            created_at_monotonic=now,
            deadline_at_monotonic=(now + self._deadline if self._deadline > 0 else None),
            timeout_seconds=self._timeout,
            cancellation_policy=CancellationPolicy.CANCEL_ON_TAKEOVER,
            origin=origin,
            source_observation_id=proposal.source_observation_id,
        )

    @staticmethod
    def _safe_relative_path(value: str) -> str | None:
        if not isinstance(value, str) or not value.strip():
            return None
        normalized = value.replace("\\", "/")
        posix = PurePosixPath(normalized)
        windows = PureWindowsPath(value)
        if (
            posix.is_absolute()
            or windows.is_absolute()
            or bool(windows.drive)
            or ":" in normalized
            or any(part in {"", ".", ".."} for part in posix.parts)
        ):
            return None
        return posix.as_posix()
