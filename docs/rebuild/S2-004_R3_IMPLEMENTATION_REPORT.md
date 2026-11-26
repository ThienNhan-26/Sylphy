> **Implementation report (2026-11-25; superseded for overall status).** This report closes S2-004-R3 only and explicitly leaves overall Phase 2 acceptance to the Project Owner. The Owner subsequently accepted Phase 2 with recorded limitations; see `PHASE_2_CLOSURE_RECORD.md`.

# S2-004-R3 — Implementation Report: Entity-Owned Configuration

**Task:** S2-004-R3 — Entity-Owned Configuration Implementation
**Phase:** 2 — Identity integration and conversation truth
**Authority:** Project Owner approved the S2-004-R2 architecture decision
**Implementer:** OpenCode
**Date:** 2026-11-25
**Deliverable ADR:** `docs/rebuild/ADR-S2-004_R2.md`

---

## 1. Files created, modified, and deleted

### Created

| File | Purpose |
|------|---------|
| `src/sylphy_entity/config.py` | Entity-owned configuration module (`EntityConfig`, `load_entity_config`, precedence constants) |
| `tests/entity_runtime/test_entity_config.py` | Entity configuration tests CFG-01..CFG-10 |
| `tests/entity_runtime/test_daemon_startup.py` | Entity daemon startup wiring tests START-01..START-07 |
| `docs/rebuild/ADR-S2-004_R2.md` | Architecture Decision Record for the approved Alternative A |

### Modified

| File | Change |
|------|--------|
| `src/sylphy_entity/daemon.py` | Added `from sylphy_entity.config import load_entity_config`; replaced unresolved `config = load_config()` with `config = load_entity_config()` |
| `src/sylphy_entity/identity.py` | Canonical Identity env var is now `SYLPHY_IDENTITY_FILE` (default); legacy alias `SYLPHY_ENTITY_IDENTITY_FILE` honored when canonical is unset |
| `docs/rebuild/ARCHITECTURE_AUDIT.md` | Targeted update to the documented startup/configuration flow after the implementation changed the Entity configuration ownership |

### Deleted

| File | Reason |
|------|--------|
| None | No files removed |

---

## 2. Exact source diffs / relevant code excerpts

### `src/sylphy_entity/daemon.py` (startup composition root)

```diff
+from sylphy_entity.config import load_entity_config
 from sylphy_entity.runtime.instance_lock import InstanceAlreadyRunning
 from sylphy_entity.runtime.paths import default_runtime_dir
 from sylphy_entity.runtime.supervisor import EntitySupervisor

-    config = load_config()
+    config = load_entity_config()
     supervisor = EntitySupervisor(
         ...
-        identity_file=config.identity_file,
+        identity_file=config.identity_file,
     )
```

The only functional change is the resolution of the previously unresolved `load_config` symbol through the Entity-owned loader; the supervisor hand-off (`identity_file=config.identity_file`) is unchanged.

### `src/sylphy_entity/config.py` (new)

```python
ENTITY_CONFIG_ENV_VAR = "SYLPHY_CONFIG"
IDENTITY_ENV_VAR = "SYLPHY_IDENTITY_FILE"
LEGACY_IDENTITY_ENV_VAR = "SYLPHY_ENTITY_IDENTITY_FILE"
DEFAULT_CONFIG_FILE = "config.toml"
DEFAULT_IDENTITY_FILE = "identity.toml"

@dataclass(frozen=True)
class EntityConfig:
    identity_file: Path
    config_file: Path

def load_entity_config(path: Path | None = None) -> EntityConfig:
    # config_path = path or SYLPHY_CONFIG or "config.toml"
    # raw = tomllib.load(config_path)
    # identity_file_value = _resolve_identity_file(raw)
    # return EntityConfig(identity_file=Path(identity_file_value), config_file=config_path)

def _resolve_identity_file(raw: dict) -> str:
    canonical = os.environ.get(IDENTITY_ENV_VAR)
    if canonical is not None and canonical.strip():
        return canonical
    legacy = os.environ.get(LEGACY_IDENTITY_ENV_VAR)
    if legacy is not None and legacy.strip():
        return legacy
    top_level = raw.get("identity_file")
    if isinstance(top_level, str) and top_level.strip():
        return top_level
    fallback = DEFAULT_IDENTITY_FILE
    ollama_table = raw.get("ollama")
    if isinstance(ollama_table, dict):
        candidate = ollama_table.get("identity_file", fallback)
        if isinstance(candidate, str) and candidate.strip():
            fallback = candidate
    return fallback
```

### `src/sylphy_entity/identity.py` (canonical Identity variable)

```diff
+IDENTITY_ENV_VAR = "SYLPHY_IDENTITY_FILE"
+LEGACY_IDENTITY_ENV_VAR = "SYLPHY_ENTITY_IDENTITY_FILE"

 def load_identity(
     identity_path: Path | str | None = None,
     *,
-    env_var: str = "SYLPHY_ENTITY_IDENTITY_FILE",
+    env_var: str = IDENTITY_ENV_VAR,
     fallback_default: bool = True,
 ) -> IdentityConfig:
```

and in `_resolve_identity_path`, when no explicit path is supplied, the canonical variable is consulted first with the legacy alias as fallback.

---

## 3. Configuration precedence actually implemented

The implemented Identity path resolution precedence (Entity-owned loader):

1. `SYLPHY_IDENTITY_FILE` (canonical) — wins when both Identity environment variables are set.
2. `SYLPHY_ENTITY_IDENTITY_FILE` (backward-compatible alias) — used when the canonical variable is unset.
3. Top-level TOML key `identity_file` in the Entity configuration file.
4. `identity_file` key in the `[ollama]` table of the Entity configuration file.
5. Default `identity.toml`.

Additional Entity-owned behaviour: the configuration file is selected by explicit path, `SYLPHY_CONFIG`, or the default (`config.toml`); a missing configuration file raises `FileNotFoundError` (explicit).

The Legacy `src/sylphy/config.py` was intentionally left unchanged.

---

## 4. Test commands and results

### New tests (S2-004-R3)

| Command | Result |
|---------|--------|
| `python -m pytest -q tests/entity_runtime/test_entity_config.py tests/entity_runtime/test_daemon_startup.py` | **17 passed** (CFG-01..CFG-10, START-01..START-07) |

### Regression suites

| Command | Result |
|---------|--------|
| `python -m compileall -q src/sylphy_entity src/sylphy tests` | **exit 0** |
| `python -m pytest -q` (default `testpaths`) | **353 passed, 2 skipped**, exit 0 |
| `python -m pytest -q tests/test_foundation.py` (Legacy config/identity suites) | **12 passed** |

The full-suite result includes the pre-existing architecture boundary test
(`tests/entity_runtime/test_architecture_boundary.py`) which enforces that no
Entity module imports the Legacy `sylphy` / `sylphy.*` package; it passed within
the full run.

---

## 5. Production runtime evidence

### Before implementation (S2-004-R1, RUNTIME-VERIFIED)

```
EXCEPTION TYPE: NameError
EXCEPTION MSG: name 'load_config' is not defined
FRAME: (...src\sylphy_entity\daemon.py, 'main', 97)
FRAME: (...src\sylphy_entity\daemon.py, '_run', 30)
```

### After implementation (S2-004-R3, RUNTIME-VERIFIED)

Command executed: `python -m sylphy_entity.daemon` (production module entrypoint,
installed console-script target `sylphy_entity.daemon:main` declared in `pyproject.toml:32`).

Evidence captured:

1. Daemon process started and passed configuration resolution (`load_entity_config`) and
   `EntitySupervisor` construction including Entity-owned Identity loading — no `NameError`.
2. Uvicorn lifecycle output: `Started server process [15404]`, `Uvicorn running on http://127.0.0.1:8765`.
3. Supervisor reached running state: `SYLPHY Entity Daemon running at http://127.0.0.1:8765`.
4. Health endpoint returned `200` with `{"status": "ok", "lifecycle": "running"}`.
5. Graceful shutdown via `POST /shutdown` returned `202 {"status": "shutdown_requested"}`; process stopped.

**Runtime verdict:** the previously failing startup transition (`config = load_config()` → `NameError`)
now completes; the production Entity startup path proceeds through configuration loading,
EntitySupervisor construction, and Identity loading to a running lifecycle state.

**Console-script entrypoint check (environmental, not code):** `sylphy-entity` was invoked as the
production entrypoint. The wrapper resolved to Python 3.15 site-packages whose editable-install
path points to `D:\CWDI\SYLPHY_Sprint_10_plus_1_fixed\sylphy_work\src`, which does not exist; the
process exited with `ModuleNotFoundError: No module named 'sylphy_entity'` (exit code 1). This is an
environment/install-state finding, not a regression from the S2-004-R3 source changes. The module
entrypoint (`python -m sylphy_entity.daemon`) was used for the successful runtime verification above.

No persistent daemon process remains running (clean shutdown performed and verified).

---

## 6. Architecture-boundary evidence

- Entity → Legacy configuration/identity imports: **absent** (new `sylphy_entity/config.py` and modified daemon contain no `sylphy` imports; existing
  `tests/entity_runtime/test_architecture_boundary.py::test_entity_package_does_not_import_legacy_sylphy_package` passes).
- Legacy configuration source (`src/sylphy/config.py`): **unchanged** during this task (Legacy `tests/test_foundation.py` configuration/identity suites: 12 passed).

---

## 7. Deviations from the S2-004-R2 approved plan and reasons

| Deviation | Reason |
|-----------|--------|
| Added `config_file: Path` field to `EntityConfig` (beyond the strictly minimal `identity_file`) | Enables explicit provenance of the loaded configuration file; does not change resolution behaviour. |
| Treated empty/whitespace-only Identity environment values as unset | Consistent canonical-wins precedence semantics; documented in ADR. |
| Updated `load_identity` default env var and legacy fallback | Required so the canonical `SYLPHY_IDENTITY_FILE` is honoured consistently at the Entity identity loader boundary. |

No deviations that change approved behaviour were accepted.

---

## 8. Remaining limitations and unresolved blockers

1. **Phase 2 acceptance** is a Project Owner decision and was not assigned here.
2. The **live conversational identity chain** (real Ollama model generating replies with the loaded personality) remains unverified without a running local model — BLOCKED / UNVERIFIED pending live inference.
3. Long-term configuration ownership consolidation (Alternative B) remains open; this task implemented Alternative A only.
4. Relative Identity path resolution (CWD-relative) is preserved as approved and was not re-resolved to absolute paths.

---

## 9. Status for S2-004-R3

**PASS** for the S2-004-R3 implementation scope:

- Entity production startup path uses Entity-owned configuration ✓
- Startup no longer raises the previous `NameError` ✓ (RUNTIME-VERIFIED)
- Resolved Identity path reaches `EntitySupervisor` and Identity loading occurs via the Entity-owned loader ✓ (TEST-VERIFIED)
- Required CFG/START tests pass (17/17) ✓
- Full regression suite passes (353 passed, 2 skipped) ✓
- Runtime evidence captured and reproducible in this environment ✓
- No Entity → Legacy configuration/identity dependency introduced ✓

**Phase 2 (overall acceptance) was NOT declared complete**; that decision belongs to the Project Owner after independent review.

---

## Stop condition

This task stopped after producing the S2-004-R3 implementation report. No Phase 3 work was started, no broader configuration refactor was performed, no Legacy modules were modified, and no identity meaning or permission behaviour was changed.
