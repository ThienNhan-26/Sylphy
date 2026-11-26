# SYLPHY Phase 2 GitHub Sync Patch

This patch contains only the Phase 2 Identity/Conversation integration files, targeted tests, and supporting decision/evidence records. It deliberately excludes `config.toml`, runtime data, local model settings, databases, credentials, caches, backups, legacy Aider history, and Stage 7/avatar files.

## Target

Repository: `ThienNhan-26/Sylphy`
Base: `main` at `10cf262fd33f60d6e45d070aa029889936b530fd`
Staging branch created: `phase2-final-sync`

## Apply on Windows PowerShell 5.1

1. Extract this archive into the canonical repository root (the folder containing `pyproject.toml`), allowing overwrite.
2. Run:

```powershell
git fetch origin
git switch phase2-final-sync
git status --short
py -3.13 -m compileall -q src/sylphy_entity
py -3.13 -m pytest -q
```

3. Review the diff. Confirm `config.toml` is unchanged and no local runtime files are staged. Then:

```powershell
git add identity.toml src/sylphy_entity tests/entity_conversation/test_identity_integration.py tests/entity_runtime/test_entity_config.py tests/entity_runtime/test_daemon_startup.py tests/entity_runtime/test_llamacpp_adapter.py docs/rebuild
git status --short
git commit -m "Phase 2: integrate Sylphy identity and llama.cpp conversation"
git push origin phase2-final-sync
```

4. Open a pull request from `phase2-final-sync` into `main`, review the file list, then merge. Do not force-push.

## Verification performed for this patch

- Python 3.13.5, pytest 9.0.2 in the authoring environment.
- `compileall`: passed.
- Entity test paths available in this source snapshot: 127 passed, 4 warnings.
- Warnings: two deprecated `websockets` imports and two synchronous tests carrying unnecessary `@pytest.mark.asyncio`.
- This local run is not a claim of Windows acceptance on the user's machine.

## Important scope note

Phase 2 is accepted and closed by the Project Owner with recorded limitations. This patch does not authorize Phase 3.

## Additional Windows verification — 2026-11-26

- Branch: phase2-final-sync
- Python: 3.13.17
- pytest: 9.2.0
- Source import: verified from the canonical sylphy_work/src directory.
- Full test suite: **392 passed, 2 skipped, 0 failed**.
- Warnings: 2 PytestWarning notices for synchronous tests unnecessarily marked with @pytest.mark.asyncio.
- Runtime: 26.00 seconds.

This is the latest Windows verification result for the staged Phase 2 source snapshot. The historical verification records above remain unchanged.
