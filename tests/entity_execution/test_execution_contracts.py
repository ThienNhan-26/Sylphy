from datetime import datetime, timezone

import pytest

from sylphy_entity.contracts.execution import (
    ExecutionResult,
    ExecutionStatus,
    Intent,
    PermissionClass,
    ResourceRequirement,
)


def _intent_kwargs(scope="sandbox:/tmp/sandbox"):
    return {
        "intent_id": "contract-intent",
        "goal_id": "contract-goal",
        "capability": "inspect_file_metadata",
        "target": "notes.txt",
        "arguments": (("relative_path", "notes.txt"),),
        "reason": "contract test",
        "preconditions": ("target_exists",),
        "permission_class": PermissionClass.OBSERVE_SANDBOX,
        "permission_scope": scope,
        "resources": (ResourceRequirement(f"filesystem:{scope}:notes.txt"),),
        "priority": 0.5,
        "expected_outcome": "file metadata evidence",
        "idempotency_key": "contract-intent",
        "created_at_monotonic": 1.0,
        "deadline_at_monotonic": 10.0,
        "timeout_seconds": 3.0,
    }


def test_intent_contract_rejects_boolean_priority_and_non_string_argument_name():
    values = _intent_kwargs()
    values["priority"] = True
    with pytest.raises(ValueError, match="priority must be numeric"):
        Intent(**values)

    values = _intent_kwargs()
    values["arguments"] = ((42, "notes.txt"),)
    with pytest.raises(ValueError, match="argument names"):
        Intent(**values)


def test_execution_result_requires_aware_timestamps_and_string_evidence_ids():
    now = datetime.now(timezone.utc)
    with pytest.raises(ValueError, match="timezone-aware"):
        ExecutionResult(
            intent_id="bad-time",
            status=ExecutionStatus.FAILED,
            started_at=now.replace(tzinfo=None),
            finished_at=now.replace(tzinfo=None),
        )

    with pytest.raises(ValueError, match="non-blank strings"):
        ExecutionResult(
            intent_id="bad-evidence",
            status=ExecutionStatus.FAILED,
            started_at=now,
            finished_at=now,
            evidence_ids=(123,),
        )
