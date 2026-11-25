"""Trusted, read-only worker for one sandbox file metadata/text inspection."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import stat
import sys

_REPARSE_POINT = 0x400


def resolve_target(root_value: str, relative_value: str) -> tuple[Path, Path]:
    root = Path(root_value).expanduser().resolve(strict=True)
    relative = relative_value.replace("\\", "/")
    posix = PurePosixPath(relative)
    windows = PureWindowsPath(relative_value)
    if (
        not relative.strip()
        or posix.is_absolute()
        or windows.is_absolute()
        or bool(windows.drive)
        or ":" in relative
        or any(part in {"", ".", ".."} for part in posix.parts)
    ):
        raise ValueError("unsafe relative path")
    current = root
    for part in posix.parts:
        current = current / part
        metadata = current.lstat()
        if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & _REPARSE_POINT:
            raise ValueError("symlink or reparse point rejected")
    target = current.resolve(strict=True)
    target.relative_to(root)
    metadata = target.stat()
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError("target must be a regular file")
    return root, target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sandbox-root", required=True)
    parser.add_argument("--relative-path", required=True)
    parser.add_argument("--mode", choices=("metadata", "read_text"), required=True)
    parser.add_argument("--max-bytes", type=int, default=65536)
    args = parser.parse_args(argv)
    try:
        _, target = resolve_target(args.sandbox_root, args.relative_path)
        metadata = target.stat()
        payload: dict[str, object] = {
            "relative_path": args.relative_path.replace("\\", "/"),
            "size_bytes": metadata.st_size,
            "modified_ns": metadata.st_mtime_ns,
            "is_file": True,
        }
        if args.mode == "read_text":
            if metadata.st_size > args.max_bytes:
                raise ValueError(f"file exceeds read limit ({args.max_bytes} bytes)")
            with target.open("rb") as stream:
                content_bytes = stream.read(args.max_bytes + 1)
            if len(content_bytes) > args.max_bytes:
                raise ValueError(f"file exceeds read limit ({args.max_bytes} bytes)")
            payload["text"] = content_bytes.decode("utf-8")
            payload["content_sha256"] = hashlib.sha256(content_bytes).hexdigest()
        evidence_body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        payload["evidence_id"] = "sha256:" + hashlib.sha256(evidence_body.encode("utf-8")).hexdigest()
        sys.stdout.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
        return 0
    except Exception as exc:
        sys.stderr.write(f"{type(exc).__name__}: {exc}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
