# S2-004-R1 — Entity Runtime Startup & Configuration Architecture Audit

**Audit Date:** 2026-11-25
**Governing authority:** `SYLPHY_MASTER_PLAN_FUTURE_HANDOVER_v3.0.md` (Version 3.0, self-contained handover edition)
**Mode:** READ-ONLY investigation — no source code, tests, configuration, or data were modified
**Prerequisite:** `docs/rebuild/S2-004_BLOCKING_EVIDENCE.md` (daemon `load_config` NameError clarification)

---

## 0. Scope, Method, and Evidence Legend

### 0.1 Scope

This audit investigates the complete SYLPHY application startup and configuration architecture, triggered by the reported `NameError` in `src/sylphy_entity/daemon.py`.

The immediate trigger was investigated, but the objective was not limited to fixing the missing symbol. The full startup path, configuration ownership, package dependency direction, identity configuration path, and architectural alternatives were examined before any implementation proposal.

### 0.2 Method (read-only)

- Source inspection of `src/sylphy_entity/daemon.py`, `src/sylphy_entity/runtime/supervisor.py`, `src/sylphy_entity/identity.py`, `src/sylphy_entity/interaction/conversation_runtime.py`, `src/sylphy_entity/runtime/paths.py`, `src/sylphy_entity/interface/*`, `src/sylphy/config.py`, `src/sylphy/identity.py`, `src/sylphy/app.py`, `config.toml`, `identity.toml`, `pyproject.toml`.
- Static import analysis (AST-based) over all files in `src/sylphy_entity/` and `src/sylphy/`.
- Runtime execution of the daemon entry function in the current Python 3.13 environment with `src` on the import path. **No persistent service was started; no background process was left running.**
- Review of governing and prior audit documents.

### 0.3 Evidence legend (per governing plan)

- **CODE-VERIFIED:** behavior visible in the exact source snapshot inspected.
- **TEST-VERIFIED:** named test passed in a named environment; command and output summary recorded.
- **RUNTIME-VERIFIED:** exercised in the actual target environment with a recorded scenario.
- **DOC-CLAIM:** a document says it is implemented or accepted, but current source/runtime has not independently proved it.
- **ENGINEERING-INFERENCE:** reasoned adaptation that must be validated.
- **PROPOSED:** a design not implemented.
- **BLOCKED / UNVERIFIED:** missing source, dependency, credentials, target hardware, or test evidence.

---

## 1. Application Startup Map (Actual Startup Sequence)

### 1.1 Production entrypoints identified

| Entrypoint | Target | Evidence |
|---|---|---|
| Console script | `sylphy-entity = "sylphy_entity.daemon:main"` | `pyproject.toml:32` — `[project.scripts]` |
| Module execution | `python -m sylphy_entity.daemon` | `src/sylphy_entity/daemon.py:109-110` — `if __name__ == "__main__": main()` |

No tests under `tests/` invoke the daemon entry function.

### 1.2 Startup sequence (source-traced)

```
pyproject.toml:32  sylphy-entity = "sylphy_entity.daemon:main"
  → src/sylphy_entity/daemon.py:main()            (def main, line 55)
    → argparse + validation                      (lines 56-96)
    → asyncio.run(_run(...))                      (line 97)
      → src/sylphy_entity/daemon.py:_run()       (async def _run, line 16)
        → line 21:  runtime_dir = default_runtime_dir()
        → lines 23-25:   if cognition_model: OllamaCognition (inline import, sylphy_entity.cognition.ollama)
        → lines 27-29:   if conversation_model: OllamaConversationGenerator (inline import, sylphy_entity.interaction.generator)
        → line 30:  config = load_config()          ← NAME ERROR (no import present)
        → line 31:  supervisor = EntitySupervisor(..., identity_file=config.identity_file, ...)
        → lines 42-47: voice output configuration (conditional)
        → line 48:  await supervisor.start()
        → lines 49-52: try/finally: await supervisor.serve_forever() / shutdown
```

`EntitySupervisor.__init__` (src/sylphy_entity/runtime/supervisor.py:56-72, 122-138):

- Parameter `identity_file: str | Path | None = "identity.toml"` (supervisor.py:71).
- Local imports (supervisor.py:122-125) from `sylphy_entity.identity`, `sylphy_entity.memory.*`, `sylphy_entity.interaction.conversation_runtime`.
- `identity = load_identity(identity_path=identity_file, fallback_default=True)` (supervisor.py:129-130).
- `self._conversation = ConversationRuntime(publisher=self, event_store=self._event_store, generator=conversation_generator, memory=..., voice=self._voice_capability, identity=identity)` (supervisor.py:132-138).

`ConversationRuntime.__init__` / `_respond` (src/sylphy_entity/interaction/conversation_runtime.py):

- `identity: Optional[IdentityConfig] = None` parameter (conversation_runtime.py:28); stored as `self._identity` (line 35).
- `_respond()` selects `self._identity.personality` when present, else hard-coded default (lines 107-112).
- Assembles `system = (personality_intro + " " + system_norms).strip() + "\n" + recalled` (line 129) and generates via `self._generator.generate(messages=..., timeout_seconds=90.0)` (line 131).

### 1.3 Observed runtime execution (this audit)

Command executed (source-inspection environment, Python 3.13):

```bash
python3 /tmp/opencode/daemon_check.py
```

which performed:

```python
import sys
sys.path.insert(0, "src")
from sylphy_entity import daemon
daemon.main()
```

**Result (RUNTIME-VERIFIED):**

```
EXCEPTION TYPE: NameError
EXCEPTION MSG: name 'load_config' is not defined
FRAME: ('...src\\sylphy_entity\\daemon.py', 'main', 97)
FRAME: ('...src\\sylphy_entity\\daemon.py', '_run', 30)
```

The failure occurs **exactly** at `daemon.py:30` (`config = load_config()`) inside `_run()`, before `EntitySupervisor` construction (which would have started at `daemon.py:31`). The module itself imports successfully; the defect is a missing name binding, not an import-time failure.

**Conclusion of §1:** the production startup path is source-traced end-to-end through `main()` → `_run()` → `EntitySupervisor` → `ConversationRuntime` → `_respond()`, but execution currently stops at `daemon.py:30`. No persistent process was started and none remains running.

---

## 2. Configuration Ownership Audit

### 2.1 Owning modules identified

| Responsibility | Owning module | Symbol | Notes |
|---|---|---|---|
| Broad application TOML config loading | `src/sylphy/config.py` | `load_config`, `AppConfig` | Legacy-owned full config surface (data_dir, ollama, memory, runtime, cognition, presence, voice, autonomy, exploration, investigation, web, vision, avatar, ...) |
| `identity_file` resolution / precedence | `src/sylphy/config.py` | `AppConfig.identity_file` | Line 268 |
| Identity loading (Entity) | `src/sylphy_entity/identity.py` | `load_identity`, `IdentityConfig` | Entity-owned loader with fallback semantics |
| Personality prompt assembly | `src/sylphy_entity/interaction/conversation_runtime.py` | `_respond()` | Entity-owned injection |

### 2.2 Exact identity configuration resolution (CODE-VERIFIED)

`src/sylphy/config.py:268`:

```python
identity_file=Path(os.environ.get("SYLPHY_IDENTITY_FILE", raw.get("identity_file", ollama.get("identity_file", "identity.toml"))),
```

Resolved precedence:

1. Environment variable `SYLPHY_IDENTITY_FILE` (overrides all).
2. Top-level TOML key `identity_file` (S2-003 fix made this first-class).
3. `[ollama]` table key `identity_file` (legacy fallback location).
4. Default `"identity.toml"`.

Current `config.toml` places `identity_file = "identity.toml"` inside the `[ollama]` table (line 12), so the effective configured value is `"identity.toml"`.

Entity loader resolution (`src/sylphy_entity/identity.py:93-132`, `_resolve_identity_path`):

1. Explicit `identity_path` provided (as passed by `EntitySupervisor` from the daemon) → used if it is a file; else CWD-relative; else `None`.
2. Environment variable `SYLPHY_ENTITY_IDENTITY_FILE` (loader default parameter, line 35).
3. Default `identity.toml` in CWD, then `../identity.toml` (repository root fallback).
4. `fallback_default=True` (EntitySupervisor call, supervisor.py:130) → deterministic default `IdentityConfig` (identity.py:135-179) instead of raising when no file is found.

### 2.3 Ownership findings

1. **Split ownership:** the broad application configuration (`AppConfig`) is owned by the legacy package (`src/sylphy/`), while the identity *content contract* and loader used by the Entity runtime is owned by `src/sylphy_entity/identity.py`. The daemon is the composition point that bridges them, and currently cannot execute that bridge.
2. **Duplicated identity semantics:** two parallel identity loaders exist —
   - legacy: `src/sylphy/identity.py:29` — `load_identity(path: Path) -> Identity`; raises `FileNotFoundError` when missing; provides `Identity.to_prompt()`; tests expect version `"0.1"`.
   - entity: `src/sylphy_entity/identity.py:32` — `load_identity(identity_path, *, env_var="SYLPHY_ENTITY_IDENTITY_FILE", fallback_default=True) -> IdentityConfig`; fallback default; version default `"1.0"`.
   Behavioral differences (required-field validation, fallback, prompt shape, version defaults) are CODE-VERIFIED from source and TEST-VERIFIED only for the Entity side.
3. **Environment variable naming divergence:** legacy config precedence uses `SYLPHY_IDENTITY_FILE`; Entity loader default env var is `SYLPHY_ENTITY_IDENTITY_FILE`. The daemon path passes the identity path explicitly (once wired), so the Entity env var is bypassed there; this remains an inconsistency to manage architecturally.
4. **Missing Entity-owned configuration module:** no `sylphy_entity` module owns a general configuration object; the Entity runtime consumes only the identity path delivered by the supervisor.
5. **S2-003 fix location:** `src/sylphy/config.py:268` was corrected so the top-level TOML key takes precedence over the `[ollama]` sub-table. **CODE-VERIFIED** current state of that expression.

### 2.4 TEST-VERIFIED configuration precedence evidence

`tests/test_foundation.py:35-47` (`test_config_loads_toml_and_environment_override`):

```python
monkeypatch.setenv("OLLAMA_MODEL", "override")
monkeypatch.setenv("SYLPHY_CONTEXT_BUDGET", "4096")
monkeypatch.setenv("SYLPHY_IDENTITY_FILE", "custom.toml")
config = load_config(config_file)
...
assert str(config.identity_file) == "custom.toml"
```

**TEST-VERIFIED:** environment override for `SYLPHY_IDENTITY_FILE` is honored through `load_config()` (tests/test_foundation.py:40-45).

---

## 3. Dependency and Import Audit (Dependency Graph)

### 3.1 Direction findings (CODE-VERIFIED by AST scan of all `*.py` under `src/sylphy_entity/` and `src/sylphy/`)

- **Entity → Legacy:** NONE found. No import in `src/sylphy_entity/` references `sylphy` or `sylphy.*`.
- **Legacy → Entity:** NONE found. No import in `src/sylphy/` references `sylphy_entity`.
- **Shared modules:** none identified at the top-level package granularity within these two source trees.

### 3.2 Entity-internal dependency edges relevant to startup

```
sylphy_entity.daemon:main
  → sylphy_entity.daemon:_run
    → sylphy_entity.runtime.paths.default_runtime_dir
    → (inline) sylphy_entity.cognition.ollama.OllamaCognition        [conditional]
    → (inline) sylphy_entity.interaction.generator.OllamaConversationGenerator  [conditional]
    → sylphy_entity.runtime.supervisor.EntitySupervisor
      → (inline) sylphy_entity.identity.load_identity
      → (inline) sylphy_entity.memory.repository.MemoryRepository
      → (inline) sylphy_entity.memory.recall.MemoryRecallService
      → (inline) sylphy_entity.interaction.conversation_runtime.ConversationRuntime
        → sylphy_entity.identity.IdentityConfig / sylphy_entity.interaction.generator.ConversationGenerator
```

### 3.3 Boundary evidence

- `tests/entity_runtime/test_architecture_boundary.py:5-19` (`test_entity_package_does_not_import_legacy_sylphy_package`) — **TEST-VERIFIED passing**: no `sylphy` / `sylphy.*` import from `src/sylphy_entity/`.
- The same boundary remains satisfied at the source level for the current daemon code **even though** the daemon calls the legacy-owned `load_config()` symbol (missing import). The structural isolation holds; the wiring defect is a symbol-resolution defect, not an isolation violation.

### 3.4 Risk notes (ENGINEERING-INFERENCE)

- Static import scan found no cycle between daemon → supervisor → submodules → conversation runtime. Full runtime cycle detection was not performed. **ENGINEERING-INFERENCE** only.
- The daemon uses function-local imports; a future refactor could reintroduce indirect legacy coupling only by explicit code addition, which the boundary test would then catch.

---

## 4. Confirmed Defects

### 4.1 DEFECT-A (S2-004-R1 focus): Missing `load_config` import in `src/sylphy_entity/daemon.py` — **CONFIRMED** (source + runtime)

- **CODE-VERIFIED:** `src/sylphy_entity/daemon.py` lines 1-13 import only stdlib + `sylphy_entity.*`; no `load_config` import; no local definition; no `import sylphy`.
- **CODE-VERIFIED:** `load_config` is defined exactly once in the repository: `src/sylphy/config.py:83`.
- **RUNTIME-VERIFIED:** executing the daemon entry function raises `NameError: name 'load_config' is not defined`, with frames confirming failure at `daemon.py:_run` line 30 → `daemon.py:main` line 97.
- **Impact:** production startup path (`sylphy-entity` → `daemon:main` → `_run` → line 30) cannot proceed; `EntitySupervisor` construction (and therefore identity loading) is unreachable.

### 4.2 Consistency defect in prior audit conclusion — **CONFIRMED as discrepancy**

- `docs/rebuild/ARCHITECTURE_AUDIT.md` §5 (Final Verdict) states **PASS**, claiming the chain was verified end-to-end with 245 passing tests.
- That verdict was written **before** the S2-004 blocking-evidence clarification. The end-to-end daemon execution was not part of that verification.
- **This R1 audit does not assign PASS to S2-004**; the end-to-end identity chain through the production daemon is currently **BLOCKED / UNVERIFIED** (see §7 conclusion).

### 4.3 No new defects confirmed in this audit

No additional missing symbols, import cycles, or authority-boundary violations were found beyond the missing `load_config` binding and the previously documented issues (identity authority boundary tests pass; permission isolation holds).

---

## 5. Unverified Assumptions

The following statements were assumed rather than independently re-proven in this audit; they remain **UNVERIFIED** unless re-checked by the owner or a later task:

1. The `sylphy-entity` console script is installed and resolves to `sylphy_entity.daemon:main` in the *owner’s runtime environment* (declared in `pyproject.toml:32` — CODE-VERIFIED declaration; installation state NOT re-verified here).
2. The daemon runs with CWD = repository root in production (relative `identity.toml` resolution assumption).
3. Behavior after the `load_config` binding is added (next failure points) — **UNVERIFIED** by execution.
4. Whether the owner wants `SYLPHY_IDENTITY_FILE` (legacy config precedence) or `SYLPHY_ENTITY_IDENTITY_FILE` (Entity loader default) to be the single identity env var — **OWNER-DIRECTED** decision pending.

---

## 6. Architectural Alternatives (PROPOSED)

Two viable approaches were considered to resolve configuration ownership without violating the Entity–Legacy isolation requirement (Master Plan D01, §5.2: legacy `src/sylphy/` is frozen as reference; `src/sylphy_entity/` is the intended rework target; the new runtime must not depend on the legacy package).

### Alternative A — Entity-local identity configuration module

- **Files likely to change:** new module under the Entity package, e.g. `src/sylphy_entity/runtime/configuration.py` or `src/sylphy_entity/config.py`, owned and consumed by the Entity runtime; `src/sylphy_entity/daemon.py` imports the Entity-owned resolver; `EntitySupervisor` consumes the resolved identity path.
- **Advantages:** Entity fully owns its identity configuration seam; no legacy import required; preserves existing Entity fallback semantics (`SYLPHY_ENTITY_IDENTITY_FILE`, default `identity.toml`, deterministic fallback); minimal blast radius for legacy.
- **Risks:** two configuration owners remain (legacy `AppConfig` for the broad app; Entity module for identity); possible future divergence; must decide whether legacy `config.toml` `[ollama].identity_file` remains honored.
- **Compatibility with existing `config.toml`:** must define whether the Entity reads `[ollama].identity_file` directly or receives the path via composition.
- **Environment variable compatibility:** supports `SYLPHY_ENTITY_IDENTITY_FILE`; relationship to `SYLPHY_IDENTITY_FILE` undecided.
- **Identity path behavior:** same resolution order as today (explicit → env → CWD → parent → fallback).
- **Required regression tests:** Entity boundary test (unchanged); identity loader tests; daemon startup fail-fast test; entity config precedence tests.

### Alternative B — Shared / consolidated configuration module with explicit Entity-facing adapter

- **Files likely to change:** a new shared configuration module/package (or migration of `AppConfig`/`load_config` into an Entity-neutral location) plus an Entity-facing adapter used by the daemon; legacy app may continue importing the shared module.
- **Advantages:** single configuration authority long-term; preserves a single user-facing config file (`config.toml`) and single env-var surface (`SYLPHY_IDENTITY_FILE`, `SYLPHY_CONFIG`).
- **Risks:** larger refactor; packaging/entry-point impact; must not introduce Entity → Legacy dependency (adapter must live on the Entity side and delegate nothing to `sylphy`); broader blast radius than the current defect warrants.
- **Compatibility with existing `config.toml`:** high (same file/keys).
- **Environment variable compatibility:** high (same env names).
- **Identity path behavior:** unchanged semantics.
- **Required regression tests:** legacy config tests, Entity boundary test, daemon startup tests, identity tests.

### Alternatives considered and not selected (for this audit only)

- Adding `from sylphy.config import load_config` directly to `src/sylphy_entity/daemon.py`: **rejected as an architectural direction** by the isolation requirement in this read-only audit (it would create the Entity → Legacy dependency the boundary forbids, even though a one-line symbol fix would unblock the immediate defect).

---

## 7. Recommended Approach (PROPOSED — not implemented) with Rationale

- **Recommendation (staff, PROPOSED):** resolve the configuration seam through an **Entity-local identity configuration module** (Alternative A), while treating the legacy `AppConfig`/`load_config` as the legacy application owner until an explicit owner decision migrates configuration ownership.
- **Rationale:** it unblocks the daemon with the smallest architectural change consistent with the Entity–Legacy isolation mandate; it reuses the already-isolated Entity loader; it preserves fallback semantics; it does not create a forbidden Entity → Legacy dependency.
- **Counter-consideration:** Alternative B yields cleaner long-term ownership but higher risk and broader scope than warranted before the immediate startup defect is fixed.

**PROPOSED final wording:** the immediate fix (add the missing binding/import in the Entity composition root) is *operationally necessary*; the architectural direction (Entity-owned identity configuration module vs. shared config module vs. direct legacy import) is a **PROPOSED** decision for owner approval.

---

## 8. Proposed Implementation Scope (PROPOSED — not implemented)

If the owner approves the recommended approach, the smallest proposed scope is:

1. Add the missing `load_config` binding/import at the Entity composition root (daemon) — **PROPOSED, owner approval required, and this audit explicitly did not perform it**.
2. Introduce or select the Entity-local configuration seam as described in §6 — **PROPOSED**.
3. Align identity environment-variable handling and version semantics — **PROPOSED**.
4. Update regression/acceptance tests — **PROPOSED**.

**No implementation steps from this audit have been performed.**

---

## 9. Regression and Acceptance Test Matrix (PROPOSED)

| # | Scenario | Evidence today | Proposed post-fix check |
|---|---|---|---|
| 1 | `config.toml` → `load_config()` → `AppConfig.identity_file` | **TEST-VERIFIED** (`tests/test_foundation.py:35-47`); **CODE-VERIFIED** (`config.py:268`) | Unchanged; add top-level-TOML-precedence test if owner wants |
| 2 | `AppConfig.identity_file` → `EntitySupervisor(identity_file=...)` → `load_identity()` | **CODE-VERIFIED** (`supervisor.py:71,129-130`); **UNVERIFIED** end-to-end via daemon | Add daemon→supervisor wiring test |
| 3 | `EntitySupervisor` → `load_identity()` → `ConversationRuntime` → `_respond()` personality injection | **TEST-VERIFIED** (`tests/entity_conversation/test_identity_integration.py`); **BLOCKED** via production daemon execution | Add startup-path integration test |
| 4 | Missing identity file → deterministic default | **TEST-VERIFIED** (`tests/entity_conversation/test_identity_integration.py:67-72,191-201`) | Unchanged |
| 5 | Identity cannot grant permissions / cannot override execution policy | **TEST-VERIFIED** (`tests/entity_conversation/test_identity_integration.py:250-314`) | Unchanged |
| 6 | Entity package boundary (no legacy import) | **TEST-VERIFIED** (`tests/entity_runtime/test_architecture_boundary.py`) | Unchanged |
| 7 | Full regression suite | **TEST-VERIFIED**: 245 passed, 2 skipped (no tests modified in S2-001–S2-004) | Re-run after any fix |

---

## 10. Final Conclusion

### 10.1 Daemon startup defect

- The missing `load_config` binding in `src/sylphy_entity/daemon.py` is **CONFIRMED** by source inspection and **RUNTIME-VERIFIED** by execution (`NameError` raised at `daemon.py:_run` line 30 via `daemon.py:main` line 97).
- The production startup path (`sylphy-entity` → `sylphy_entity.daemon:main` → `_run()`) is therefore **FAILED** to execute as written and is **BLOCKED** at the configuration-loading transition.

### 10.2 Identity configuration path

- The intended chain `config.toml → load_config() → config.identity_file → EntitySupervisor → load_identity() → ConversationRuntime → _respond()` is **CODE-VERIFIED** step-by-step, **TEST-VERIFIED** at component level, but its execution through the production daemon is **BLOCKED / UNVERIFIED** pending resolution of the missing binding.

### 10.3 Final verdict for this audit

- **CONFIRMED:** missing `load_config` import/binding in the Entity daemon; single source definition located in the legacy-owned module; Entity-side structural isolation otherwise intact.
- **FAILED / BLOCKED:** current production daemon startup path cannot reach configuration-driven identity loading.
- **UNVERIFIED:** post-fix behavior and the selected long-term configuration ownership architecture.

### 10.4 S2-004 status

- **This audit does not assign PASS to S2-004.** The prior `PASS` verdict in `docs/rebuild/ARCHITECTURE_AUDIT.md` (§5) was not re-validated through the production daemon and is superseded for end-to-end purposes by the present findings.

---

## Deliverables produced by this sub-task

- `docs/rebuild/S2-004_R1_STARTUP_CONFIGURATION_AUDIT.md` — this document.

## Constraints honored

- READ-ONLY source investigation; no source code, tests, configuration, or data modified.
- No fix, import addition, refactor, or implementation performed.
- No Phase 3 work started; no new architecture introduced without source evidence.
- No persistent daemon process left running.

## Stop condition

**STOPPED** after producing this audit. No further tasks or phases were initiated, and no implementation work was performed. The project owner must review and approve the architecture before any implementation begins.
