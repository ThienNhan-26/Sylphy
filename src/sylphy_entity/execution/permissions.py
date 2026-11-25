"""Explicit sandbox permission checks immediately before execution."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import stat
from typing import Protocol

from sylphy_entity.contracts.execution import Intent, PermissionClass, ResourceMode


class PermissionDenied(PermissionError):
    pass


@dataclass(frozen=True, slots=True)
class AuthorizedTarget:
    root: Path
    path: Path
    relative_path: str
    permission_class: PermissionClass
    permission_scope: str
    size_bytes: int


class PermissionStateStore(Protocol):
    def save_permission(
        self, permission_id: str, permission_class: str, permission_scope: str,
        *, state: str = "GRANTED", metadata: dict | None = None,
    ) -> None: ...
    def load_permissions(self) -> tuple[dict, ...]: ...


class PermissionPolicy:
    """Grants only read-only access under one configured sandbox root."""

    _REPARSE_POINT = 0x400

    def __init__(
        self, sandbox_root: Path, *, max_read_bytes: int = 64 * 1024,
        state_store: PermissionStateStore | None = None,
    ) -> None:
        if max_read_bytes < 1:
            raise ValueError("max_read_bytes must be positive")
        self._configured_root = Path(sandbox_root).expanduser()
        self._root: Path | None = None
        self.max_read_bytes = max_read_bytes
        self._revoked_scopes: set[str] = set()
        self._state_store = state_store
        self._persisted_state_loaded = False

    @property
    def root(self) -> Path:
        if self._root is None:
            self._root = self._configured_root.resolve(strict=True)
            if not self._root.is_dir():
                raise NotADirectoryError(self._root)
        return self._root

    @property
    def scope(self) -> str:
        return f"sandbox:{self.root}"

    def _load_persisted_state(self) -> None:
        if self._persisted_state_loaded:
            return
        self._persisted_state_loaded = True
        if self._state_store is None:
            return
        for record in self._state_store.load_permissions():
            if record.get("permission_scope") != self.scope or record.get("permission_class") != "*":
                continue
            if record.get("state") == "REVOKED":
                self._revoked_scopes.add(self.scope)
            elif record.get("state") == "GRANTED":
                self._revoked_scopes.discard(self.scope)

    def revoke_scope(self, scope: str) -> None:
        if scope != self.scope:
            raise PermissionDenied("cannot revoke a scope not owned by this policy")
        self._load_persisted_state()
        self._revoked_scopes.add(scope)
        if self._state_store is not None:
            self._state_store.save_permission(
                f"scope:{scope}", "*", scope, state="REVOKED",
                metadata={"source": "user_or_runtime_policy"},
            )

    def restore_scope(self, scope: str) -> None:
        if scope != self.scope:
            raise PermissionDenied("cannot restore a scope not owned by this policy")
        self._load_persisted_state()
        self._revoked_scopes.discard(scope)
        if self._state_store is not None:
            self._state_store.save_permission(
                f"scope:{scope}", "*", scope, state="GRANTED",
                metadata={"source": "user_or_runtime_policy"},
            )

    def is_revoked(self, scope: str) -> bool:
        self._load_persisted_state()
        return scope in self._revoked_scopes

    def authorize(self, intent: Intent) -> AuthorizedTarget:
        if intent.permission_scope != self.scope:
            raise PermissionDenied("intent permission scope does not match the configured sandbox")
        expected = {
            "inspect_file_metadata": PermissionClass.OBSERVE_SANDBOX,
            "read_sandbox_text": PermissionClass.READ_SANDBOX,
        }.get(intent.capability)
        if expected is None:
            raise PermissionDenied(f"capability is not permitted: {intent.capability}")
        if intent.permission_class is not expected:
            raise PermissionDenied("permission class does not match capability")
        if self.is_revoked(intent.permission_scope):
            raise PermissionDenied("permission scope has been revoked")
        relative = self._safe_relative_path(intent.target)
        if relative is None:
            raise PermissionDenied("target is not a safe relative path")

        root = self.root
        expected_resource = f"filesystem:{self.scope}:{relative}"
        if len(intent.resources) != 1 or (
            intent.resources[0].resource_key != expected_resource
            or intent.resources[0].mode is not ResourceMode.SHARED
        ):
            raise PermissionDenied("resource claim must be a shared lease for the exact authorized sandbox path")
        current = root
        try:
            for part in PurePosixPath(relative).parts:
                current = current / part
                metadata = current.lstat()
                attributes = getattr(metadata, "st_file_attributes", 0)
                if stat.S_ISLNK(metadata.st_mode) or attributes & self._REPARSE_POINT:
                    raise PermissionDenied("symlinks and reparse points are not permitted")
            resolved = current.resolve(strict=True)
            resolved.relative_to(root)
            metadata = resolved.stat()
        except PermissionDenied:
            raise
        except (OSError, ValueError) as exc:
            raise PermissionDenied(f"sandbox target cannot be safely resolved: {exc}") from exc
        if not stat.S_ISREG(metadata.st_mode):
            raise PermissionDenied("target must be a regular file")
        if intent.capability == "read_sandbox_text" and metadata.st_size > self.max_read_bytes:
            raise PermissionDenied(f"file exceeds READ_SANDBOX limit ({self.max_read_bytes} bytes)")
        return AuthorizedTarget(
            root=root,
            path=resolved,
            relative_path=relative,
            permission_class=expected,
            permission_scope=self.scope,
            size_bytes=metadata.st_size,
        )

    @staticmethod
    def _safe_relative_path(value: str) -> str | None:
        if not value or not value.strip():
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
