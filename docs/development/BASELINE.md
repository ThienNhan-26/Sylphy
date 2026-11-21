# SYLPHY G0 Baseline Documentation

## Repository Identification

| Property | Value |
|---|---|
| Canonical repository path | `D:\CWDI\SYLPHY_Sprint_10_plus_1_fixed\sylphy_work` |
| Original reference path | `D:\CWDI\Sylphy` (must remain untouched) |
| Git branch | `master` |
| Current commit | `e7510cb` — "G0 baseline: add .gitignore, untrack runtime-generated and user data files" |
| Previous commit | `ee85eef` — "Initial import of Sprint 10 plus 1 fixed version" |

## Python Version and Environment

- **Python**: 3.10.11 (`C:\Users\dafen\AppData\Local\Python\pythoncore-3.10-64\python.exe`)
- **Platform**: Windows 10/11 (win32)
- **Pip packages installed**: pytest 9.2.0, numpy 2.2.6, tomli 2.5.0, ollama (installed, no `__version__` attribute), Pillow

Note: Python 3.10 does not include `tomllib` (stdlib). The project uses `tomli` as a fallback (`tomli>=2; python_version < '3.11'`).

## Environment Setup

### 1. Create and activate virtual environment

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1
```

If PowerShell blocks activation:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

### 2. Install dependencies

```powershell
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Optional extras:

```powershell
python -m pip install -e ".[vision]"   # opencv-python-headless
python -m pip install -e ".[voice]"    # faster-whisper, kokoro-onnx, sounddevice, webrtcvad-wheels
```

## Dependency Installation Commands

| Purpose | Command |
|---|---|
| Core + dev (tests) | `python -m pip install -e ".[dev]"` |
| Vision optional extra | `python -m pip install -e ".[vision]"` |
| Voice optional extra | `python -m pip install -e ".[voice]"` |
| Upgrade pip | `python -m pip install --upgrade pip` |

## Test Commands

| Purpose | Command |
|---|---|
| Unit tests (default, excludes integration) | `python -m pytest -q` |
| Test collection only | `python -m pytest --collect-only -q` |
| Integration tests (requires Ollama + model) | `$env:SYLPHY_TEST_MODEL="MODEL:TAG"; python -m pytest -q -m integration` |
| Specific test file | `python -m pytest tests/test_voice.py -v` |

## Current Test Results

- **Test collection**: 314/315 tests collected (1 integration test deselected by default via `addopts = "-m 'not integration'"`)
- **Unit tests run**: All 314 unit tests passed
- **Integration tests**: Not executed (requires Ollama running locally and a configured test model via `SYLPHY_TEST_MODEL` environment variable)

## Known Failures

| Test | Issue | Status |
|---|---|---|
| `tests/test_ollama_integration.py::test_local_ollama_answers_when_available` | Requires a running Ollama server and a configured model (`SYLPHY_TEST_MODEL` env var). Not included in default test run. | Not executed — hardware/runtime dependency |

## Runtime Configuration Locations

| File | Purpose |
|---|---|
| `config.toml` | Main application configuration (Ollama host/model, memory, cognition, presence, avatar, vision, voice, exploration, web) |
| `identity.toml` | SYLPHY identity definition (name, personality, communication style, relationship, boundaries, version) |
| `identity.md` | Identity reference document |

## Database Locations

| Path | Purpose |
|---|---|
| `data\sylphy.sqlite3` | SQLite database: sessions, messages, memories, cognition tasks, exploration questions, investigation records |
| `logs\sylphy.log` | Rotating application log |

Both `data/` and `logs/` are excluded from Git tracking (see `.gitignore`).

## Files Excluded from Git

See `.gitignore` for the full list. Key exclusions:

- `.venv/` — virtual environment
- `__pycache__/` and `*.py[cod]` — Python bytecode caches
- `.pytest_cache/` — pytest cache
- `*.egg-info/` — build artifacts
- `data/` — runtime database and user data
- `logs/` — application logs
- `.env` / `*.env` — environment secrets
- `Thumbs.db`, `.DS_Store` — OS metadata

## Safe Startup and Shutdown Instructions

### Startup

1. Ensure Ollama is running locally: `ollama --version` and `ollama list`
2. Verify the configured model in `config.toml` under `[ollama].model` is installed in Ollama
3. Activate the virtual environment: `.\.venv\Scripts\Activate.ps1`
4. Start SYLPHY: `python -m sylphy.app` or `sylphy` (after editable install)
5. At the session prompt: `n` = new session, `l` = list/resume saved session, `q` = quit

### Shutdown

- Inside a conversation: `/quit` returns to session selection
- At session selection: `q` quits cleanly
- Or press `Ctrl+C` / `Ctrl+D` — the application handles `KeyboardInterrupt`/`EOFError` and calls `runtime.shutdown()` in a `finally` block

### Do NOT

- Do not delete `data\sylphy.sqlite3` without first backing it up (contains conversation and memory data)
- Do not modify `identity.toml` or `config.toml` while SYLPHY is running
- Do not run destructive tests against user databases

## Rollback Procedure

The original reference repository at `D:\CWDI\Sylphy` must remain untouched and serves as the rollback source.

If the canonical repository needs to be rolled back:

1. Do not modify or delete `D:\CWDI\Sylphy`
2. Review git history in the canonical repo: `git log --oneline`
3. To revert the most recent commit: `git revert <commit>` (creates a new commit that undoes changes)
4. To inspect a previous state: `git show <commit>:<path>` (read-only, does not modify working tree)
5. Never `git reset --hard` or `git checkout -f` — these would discard changes

## Verification Boundary

Per `INSTALL_WINDOWS.md`: Live Ollama inference, Windows camera drivers, microphone/speaker devices, avatar asset loading, and installation on the target machine remain unverified in this handoff. The `tests/test_ollama_integration.py` integration test validates the Ollama path when a local server and model are available.