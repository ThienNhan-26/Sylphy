from pathlib import Path

import pytest

from sylphy_entity.contracts.execution import PermissionClass, ResourceMode, ResourceRequirement
from sylphy_entity.execution.permissions import PermissionDenied, PermissionPolicy


def test_authorizes_metadata_observation_only_under_exact_sandbox_scope(sandbox, intent_factory):
    policy = PermissionPolicy(sandbox)
    intent = intent_factory(scope=policy.scope)
    authorized = policy.authorize(intent)
    assert authorized.path == sandbox / "notes.txt"
    assert authorized.permission_class is PermissionClass.OBSERVE_SANDBOX


def test_rejects_mismatched_or_simulated_scope(sandbox, intent_factory):
    policy = PermissionPolicy(sandbox)
    intent = intent_factory(scope="simulated:sandbox")
    with pytest.raises(PermissionDenied, match="scope"):
        policy.authorize(intent)


def test_rejects_path_traversal_absolute_paths_and_ads(sandbox, intent_factory):
    policy = PermissionPolicy(sandbox)
    for path in ("../outside.txt", "/etc/passwd", r"C:\Windows\win.ini", "notes.txt:secret"):
        with pytest.raises(PermissionDenied):
            policy.authorize(intent_factory(path, scope=policy.scope))


def test_rejects_symlinks_and_reparse_like_links(sandbox, intent_factory, tmp_path):
    outside = tmp_path.parent / "outside-secret.txt"
    outside.write_text("not in sandbox", encoding="utf-8")
    link = sandbox / "shortcut.txt"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is not permitted in this environment")
    policy = PermissionPolicy(sandbox)
    with pytest.raises(PermissionDenied, match="symlink"):
        policy.authorize(intent_factory("shortcut.txt", scope=policy.scope))


def test_read_permission_is_explicit_and_size_bounded(sandbox, intent_factory):
    policy = PermissionPolicy(sandbox, max_read_bytes=8)
    intent = intent_factory(
        "notes.txt",
        capability="read_sandbox_text",
        permission=PermissionClass.READ_SANDBOX,
        scope=policy.scope,
    )
    with pytest.raises(PermissionDenied, match="exceeds READ_SANDBOX limit"):
        policy.authorize(intent)


def test_rejects_directories_and_missing_files(sandbox, intent_factory):
    policy = PermissionPolicy(sandbox)
    for path in ("sub", "missing.txt"):
        with pytest.raises(PermissionDenied):
            policy.authorize(intent_factory(path, scope=policy.scope))


def test_rejects_resource_claim_that_does_not_match_authorized_path(sandbox, intent_factory):
    from dataclasses import replace

    policy = PermissionPolicy(sandbox)
    intent = intent_factory(scope=policy.scope)
    forged = replace(
        intent,
        resources=(ResourceRequirement("filesystem:*", ResourceMode.SHARED),),
    )
    with pytest.raises(PermissionDenied, match="exact authorized sandbox path"):
        policy.authorize(forged)


def test_scope_can_be_revoked_and_restored(sandbox, intent_factory):
    policy = PermissionPolicy(sandbox)
    intent = intent_factory(scope=policy.scope)
    policy.revoke_scope(policy.scope)
    with pytest.raises(PermissionDenied, match="revoked"):
        policy.authorize(intent)
    policy.restore_scope(policy.scope)
    assert policy.authorize(intent).path == sandbox / "notes.txt"
