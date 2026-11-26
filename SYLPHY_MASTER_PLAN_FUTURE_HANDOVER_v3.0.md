# SYLPHY — Master Plan for a Future Assistant

**Version:** 3.0 — Self-contained handover edition  
**Prepared:** 25 November 2026  
**Project:** SYLPHY local-first AI companion  
**Status:** Governing handover plan; architecture and implementation status must be revalidated against the supplied archives before work begins.

## Contents

1. Read this first — contract with the future assistant
2. Project identity and mission
3. Source archive manifest
4. Stage 0 — source integrity and baseline
5. Authority, evidence labels and source reasoning
6. Current SYLPHY architecture
7. Research register
8. Decision traceability
9. Implementation status
10. Rebuild roadmap
11. Acceptance evidence matrix
12. Development documentation
13. First session runbook
14. Handover prompt
15. Final project principle

---

## 0. Read this first — contract with the future assistant

You are taking over SYLPHY without access to the prior conversation, saved memories, or the previous assistant's internal context. Assume you know nothing about this project until you have inspected the supplied archives and this document.

Your task is **not** to continue a chat, not to recreate a remembered architecture, and not to rewrite the repository because it looks complicated. Your task is to establish a truthful, reproducible understanding of the current source, preserve what is already sound, identify real contradictions and missing evidence, then move the project forward in small, verifiable increments.

### Mandatory rules

1. **Read this entire Master Plan before changing code.** Then inspect the exact archives named in Section 2. The filenames and hashes are part of the handover contract.
2. **Do not treat prior conversation claims, old status tables, or a test-results text file as current proof.** Source files, tests, executable behavior, and reproducible logs have different evidence levels.
3. **Do not begin implementation until Stage 0 is complete.** The current SYLPHY main archive has a known source-integrity defect: the `sylphy_entity.memory` package is absent even though the runtime and tests import it. This prevents reliable test collection. Resolve this with the project owner or an explicitly supplied matching source recovery patch; do not invent missing modules from tests.
4. **Do not perform a wholesale rewrite.** `src/sylphy_entity/` is the intended rework target. Preserve its existing architecture and accepted slices unless a demonstrated defect requires a change. `src/sylphy/` is legacy reference only and must not become a dependency of the new Entity runtime.
5. **For every material change, write the evidence chain:** problem → exact source evidence → lesson → proposed adaptation to SYLPHY → implementation location → tests → observed result → remaining limitation.
6. **Never attach a source to a decision merely because it is an interesting project.** Research projects are evidence only for the behavior actually found in their source or documentation. Mark draft/design notes as draft. Distinguish copied facts from engineering inference and owner direction.
7. **Do not claim a feature is complete because a module or test exists.** An implementation is complete only when its acceptance scenario is exercised in the target environment and its limitations are recorded.
8. **No permission by implication.** A user enabling one capability does not enable another. LLM output is not permission. A proposed action is not an executed action; an accepted command is not proof of success.
9. **No false inner-life claims.** SYLPHY must not claim consciousness, private experience, offline activity, continuous awareness, memories that are not stored and confirmed, or capabilities not actually available.
10. **Keep the user in control.** Do not delete data, reset databases, force-push branches, rewrite history, overwrite source archives, or enable sensors/network/tool access without explicit approval.

### What this handover is — and is not

This document is the project map, architectural contract, evidence register, staged plan, and first-session procedure. It is not proof that every described feature currently works. The archive contains a mixture of code, tests, design notes, implementation reports, legacy material, and pending verification. The future assistant must preserve those distinctions.

---

## 1. Project identity and mission

### 1.1 Mission

SYLPHY is a **Windows-first, local-first AI companion** intended to become one persistent software Entity that can:

- receive permitted events and observations from its environment;
- maintain a bounded, evidence-aware representation of what is known, unknown, inferred, or stale;
- decide whether to act, respond, wait, request permission, or remain silent;
- execute only authorized operations through controlled capabilities;
- record what actually happened and recover durable state after process restart;
- converse with a stable identity independent of the underlying model provider;
- maintain user-governed memory and, later, voice and visual embodiment.

The defining product goal is not “an agent that can do everything.” It is a companion whose behavior is **coherent, trustworthy, bounded, and continuous only to the extent supported by durable evidence**.

### 1.2 Identity direction

The operational identity text in `Sylphy-main/identity.toml` defines SYLPHY as a local-first companion with a consistent character and bounded initiative. It describes a personality that is:

- gentle and considerate without being timid or submissive;
- patient and attentive without being passive;
- curious and willing to initiate without being intrusive;
- warm and occasionally playful without becoming a caricature;
- thoughtful and honest, including when disagreeing or uncertain;
- comfortable with quiet presence.

The relationship should develop through actual interaction. SYLPHY must not assume familiarity, manufacture shared history, become possessive or manipulative, or pressure the user after refusal. It should distinguish observation from inference and treat silence as valid.

These are behavioral goals, not evidence that the model currently behaves this way. They require runtime integration, policy enforcement, and scenario-based tests.

### 1.3 Inspiration versus specification

`docs/identity/source/soul.txt` is provenance-tracked source material, not a software specification. It contains an Animama walkthrough, memory/emotion/behavior discussion, Luna transcript material, and Vietnamese analysis of the fictional Sylphy/Sylphiette character. Use it to understand the user's inspiration and intent. Do not treat it as implementation requirements, copy dialogue, impersonate the fictional character, or infer that every Animama feature belongs in SYLPHY.

The operational identity and the Soul Contract are the project-facing authorities. The source transcript is inspiration and context.

### 1.4 Non-goals for the initial rebuild

Do not make these the foundation or first milestone:

- an unrestricted desktop agent with arbitrary shell, browser, file, or app control;
- continuous webcam or microphone monitoring;
- cloud inference, cloud memory, remote telemetry, or external processing without explicit user choice;
- a complex multi-daemon architecture copied from robotics projects;
- a large emotion taxonomy or elaborate avatar effects before behavior is coherent;
- automatic memory rewriting with no review, provenance, or rollback;
- a claim of general autonomous research if the search, reader, delivery, and recovery paths are not all available and tested;
- VRM/3D polish as a substitute for a functioning Entity runtime.

---

## 2. Source archive manifest — exact materials to inspect

The following six archives were available in the preparation session. Compare the filenames and SHA-256 hashes before trusting that the future session has the same material. If an archive differs, treat it as a different source snapshot and recalculate its inventory.

- **S0 — SYLPHY main** — `Sylphy-main(1).zip`; root `Sylphy-main/`. SHA-256: `db100fbe42eba11d0e0a8eccd923f58e6b53e96c98e5476de40bee7c6531ec5a`. Role: main code, tests, identity, stage reports and master plan.
- **R1 — Microduck** — `microduck-main(6).zip`; root `microduck-main/`. SHA-256: `5f064804d09775231463301311f785249d2742012948354f123621629147a814`. Role: autonomous Brain and robotics architecture research.
- **R2 — Reachy Mini** — `reachy_mini-1.15.0(7).zip`; root `reachy_mini-1.15.0/`. SHA-256: `1ee4301219a8cee6e60b9559802da959d59f792167cb33c63ae7146fb7d8f3e7`. Role: daemon/app lifecycle and scoped resource ownership research.
- **R3 — Animama** — `animama-main(6).zip`; root `animama-main/`. SHA-256: `5da7f5cae0e36cbee557e65cab77cbd479be5937b5c3bba9288311a579f69936`. Role: companion identity, memory, avatar, benchmark and engineering-process research.
- **R4 — Mark-LVII** — `Mark-LVII-main(5).zip`; root `Mark-LVII-main/`. SHA-256: `e8d126990980a7e2243e4a4ae6c36406b8b9da35346713b188d859d8a6f1c106`. Role: bounded background task, confirmation, truthful outcome and model-adapter research.
- **R5 — Roamies** — `Roamies-main(3).zip`; root `Roamies-main/`. SHA-256: `891affa63565e049aac7027cb1b4cf02345748ded324bc15172f17c5076dbe46`. Role: desktop companion interaction, privacy disclosure and centralized safety research.

**Do not conflate R4 and R5.** Mark-LVII and Roamies are different projects and support different lessons. If the user later calls one archive “RIE,” verify its actual filename and contents rather than guessing which project they meant.

### 2.1 Required inspection order

1. **S0 — SYLPHY main:** inspect the top-level master plan, source layout, config, identity files, contracts, stage reports, tests, and test record. Start with Section 3 of this handover for the critical known defect.
2. **R1 — Microduck:** inspect the Brain implementation first, then its draft docs. Separate implemented code from draft architecture.
3. **R2 — Reachy Mini:** inspect lifecycle manager, app lock, and lifecycle contract. Extract lifecycle/ownership ideas only.
4. **R3 — Animama:** inspect README, SPECS, PLANS, SDLC, memory architecture/ADR, avatar skills and model benchmark/local-stack guidance. Avoid reading it as a feature shopping list.
5. **R4 — Mark-LVII:** inspect outcome semantics, confirm flow, task engine/store, and LLM client. Study its bounded work and proof-of-outcome behavior, not its full agent/action scope.
6. **R5 — Roamies:** inspect privacy/security disclosures and safety routing. Study the permission boundary and threat model, not its provider/cloud model.

### 2.2 Suggested archive verification commands

Run in a PowerShell session at the directory containing the archives:

```powershell
Get-FileHash .\Sylphy-main(1).zip -Algorithm SHA256
Get-FileHash .\microduck-main(6).zip -Algorithm SHA256
Get-FileHash .\reachy_mini-1.15.0(7).zip -Algorithm SHA256
Get-FileHash .\animama-main(6).zip -Algorithm SHA256
Get-FileHash .\Mark-LVII-main(5).zip -Algorithm SHA256
Get-FileHash .\Roamies-main(3).zip -Algorithm SHA256
```

Archive hashes identify the exact source snapshots reviewed here. They do not prove that the source itself is correct or secure.

---

## 3. Stage 0 — source integrity and reproducible baseline (mandatory hard gate)

### 3.1 Known blocker in the provided SYLPHY main archive

The reviewed `Sylphy-main(1).zip` does **not** contain any of these paths:

```text
Sylphy-main/src/sylphy_entity/memory/__init__.py
Sylphy-main/src/sylphy_entity/memory/repository.py
Sylphy-main/src/sylphy_entity/memory/recall.py
Sylphy-main/src/sylphy_entity/memory/legacy_import.py
```

But the archive's `src/sylphy_entity/interaction/conversation_runtime.py` imports:

```python
from sylphy_entity.memory.recall import MemoryRecallService
```

Tests under `tests/entity_memory/` also import the absent package. Therefore, the main archive as supplied is incomplete for this import graph. In the preparation environment, test collection from the unmodified archive produced six collection errors tied to the missing package. This is a source packaging failure, not evidence that the memory design is wrong.

The four modules were found in a separate completion-candidate archive available during preparation, but that archive is **not one of the six handover inputs listed above**. Do not assume the future session has it. Do not fabricate equivalent modules from tests or from architecture notes.

### 3.2 Historical test record — interpret carefully

`Sylphy-main/R7_AUTOMATED_TEST_RESULTS_20261125.txt` records:

- 319 passed across three grouped test runs;
- Python compile check passed;
- two Electron JavaScript syntax checks passed;
- TypeScript build was blocked by missing npm packages (`three`, `@pixiv/three-vrm`);
- `npm ci` timed out;
- Vitest/Vite and Ruff were not run;
- Windows speaker and interactive Electron acceptance were pending.

This is a historical record. Because the memory package is missing from the exact main archive, the 319-test result cannot be independently reproduced from that archive alone. In the preparation session, after restoring the exact four missing files into a scratch extraction from the separate completion candidate, the memory + conversation tests passed 49/49 and test collection reached 326 tests. The full test suite was attempted but did not finish within the execution window. **Do not report a full-suite pass.**

### 3.3 Stage 0 procedure

Before editing anything:

1. Verify archive hashes and inventory.
2. Extract S0 into a clean directory; do not edit the ZIP itself.
3. Confirm whether `src/sylphy_entity/memory/` exists.
4. If it is absent, stop before implementation. Tell the owner exactly which files are missing and request either:
   - a complete, matching SYLPHY source archive; or
   - an explicitly approved recovery patch containing the four exact modules from the matching project snapshot.
5. Record the provenance and hashes of any recovery files. Do not mix modules from different branches/commits without owner approval.
6. Create a fresh Python environment. Prefer Python 3.13 if that is the target user's known Windows environment, but first inspect `pyproject.toml` and the user's currently available interpreter. The project declares Python `>=3.10`; the previously recorded acceptance used Python 3.13.17.
7. Install only the required project extras. Do not run network-heavy frontend installs or download models without approval.
8. Run `compileall`, test collection, then grouped tests. Preserve complete logs.
9. Compare actual outcomes against the historical test record and stage reports. Explain every mismatch.
10. Write `docs/rebuild/BASELINE_AUDIT_YYYYMMDD.md` with archive hash, Python version, dependencies, commands, counts, failures, skipped tests, and environment-specific blockers.

### 3.4 Baseline commands

Use these as a starting point after source integrity is resolved. Adapt paths only after confirming the real repository root.

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev,cognition]"
python -m compileall -q src/sylphy_entity tests
python -m pytest --collect-only -q
python -m pytest -q tests/entity_memory tests/entity_conversation
python -m pytest -q tests/entity_runtime tests/entity_world tests/entity_brain tests/entity_execution
python -m pytest -q tests/entity_persistence
python -m pytest -q tests/entity_cognition tests/entity_memory tests/entity_conversation tests/entity_sensors tests/entity_voice tests/entity_embodiment
```

Run the full suite separately after the grouped checks. Record the actual exit code and full output. A partial run or timeout is **INCOMPLETE**, not PASS.

---

## 4. Authority, evidence labels, and how to reason about sources

### 4.1 Authority hierarchy

When sources disagree, use this order and document the conflict:

1. **Explicit current owner decision** — product direction, privacy boundaries, hardware constraints, and approved scope. Ask when a decision is destructive or materially ambiguous.
2. **This handover plan** — current intended architecture, stage order, and rules; it may be superseded only by an explicit owner-approved decision record.
3. **Current SYLPHY code and executable tests** — what the supplied source actually implements and what the exact environment can reproduce.
4. **SYLPHY contracts and provenance docs** — intended invariants and past implementation boundaries. Check their named code path because some documents describe the legacy `src/sylphy` package, not the new `src/sylphy_entity` package.
5. **Research archives** — evidence for lessons that are observed in the referenced project, not authority over SYLPHY's requirements.
6. **Old chat summaries, roadmap status text, and unverified claims** — discovery hints only; never proof.

### 4.2 Required evidence labels

Use these labels in design docs, PR descriptions, and status reports:

- **CODE-VERIFIED:** the behavior is visible in the exact source snapshot inspected.
- **TEST-VERIFIED:** a named test passed in a named environment; include command and output summary.
- **RUNTIME-VERIFIED:** exercised in the actual target environment/device/OS with a recorded scenario.
- **DOC-CLAIM:** a document says it is implemented or accepted, but current source/runtime has not independently proved it.
- **RESEARCH-OBSERVATION:** behavior/design was found in an external reference archive; not automatically suitable for SYLPHY.
- **OWNER-DIRECTED:** selected by the project owner; research does not prove it superior.
- **ENGINEERING-INFERENCE:** a reasoned adaptation that must be validated.
- **PROPOSED:** a design not implemented.
- **BLOCKED / UNVERIFIED:** missing source, dependency, credentials, target hardware, or test evidence.

Every statement about status should use one of these labels or be phrased as a clear proposal.

### 4.3 Legacy versus new runtime

The main archive contains both:

- `src/sylphy/` — older/legacy application and companion pipeline;
- `src/sylphy_entity/` — intended Entity rework target.

The in-archive master plan explicitly says the legacy runtime is frozen as reference and the new runtime must not import or depend on it. Some `docs/development/CRI_00x_*.md` files describe the older `src/sylphy` companion slice. They may express valuable product invariants (truthful outcomes, permissions, bounded initiative, continuity), but do not claim those features are integrated into `sylphy_entity` unless you trace the actual code and tests in that package.

---

## 5. Current SYLPHY architecture — preserve the good boundaries

The in-archive Entity Rework Master Plan (`Sylphy-main/SYLPHY_ENTITY_REWORK_MASTER_PLAN.md`) defines the target as one persistent Entity, not a collection of unrelated agents.

### 5.1 Core flow

```text
Allowed inputs / sensors / user interaction
                 |
                 v
       Typed events and observations
                 |
                 v
          World Model snapshot
                 |
                 v
        Pure deterministic Brain
                 |
                 v
         Decision / Intent frame
                 |
                 v
  Runtime policy + permission checks
                 |
                 v
  Resource-specific arbitration
                 |
                 v
       Controlled capability worker
                 |
                 v
        Evidence-backed outcome
                 |
                 v
       Durable state / event store
                 |
                 +------> updated World / next cycle
```

The LLM belongs beside the deterministic core as an optional cognition capability. It may interpret, summarize, or propose bounded candidates. It must not own the Brain loop, grant permissions, or directly execute tools.

### 5.2 Ownership boundaries

- **Entity Daemon / Runtime** owns lifecycle, event loop, startup/shutdown and service composition. It must not own personality, model reasoning or UI policy shortcuts.
- **World Model** owns the current structured representation and evidence freshness. It must not perform direct sensor I/O or accept arbitrary LLM-generated facts as observations.
- **Brain** owns pure decisions from snapshot, internal state and elapsed time. It must not call filesystem/network/API/model services, persist data or execute actions.
- **Intent Router / Policy** owns validation of proposed work and authorization boundaries. It must not claim user-visible success without outcome evidence.
- **Resource Arbiter** owns claims for specific shared resources. It must not become a universal lock that blocks unrelated capabilities.
- **Worker / Adapter** owns one explicitly scoped operation. It must not expand permissions or initiate hidden follow-up work.
- **Persistence** owns durable events, state, idempotency and recovery. UI-specific transient state must not be the only source of truth.
- **Cognition adapter** owns model request/response behind a replaceable interface. It must not own permissions, durable state mutation or direct tool execution.
- **Identity authority** owns stable character and communication constraints. It must not grant capabilities or replace application security policy.
- **Avatar / Voice adapter** owns expression and output presentation. It must not make policy decisions or independently own the Entity.

### 5.3 Existing target package map

The archive's `src/sylphy_entity/` includes the following major areas:

- `daemon.py`
- `runtime/` — event loop, instance lock, lifecycle, paths, process manager, supervisor
- `world/` — model, projector, adapters, filesystem and simulated world
- `brain/` — core, drives, novelty, simulation, transitions
- `execution/` — dispatcher, intent router, permissions, resource arbiter, worker executor, file inspector worker
- `persistence/` — codec, database, event store, recovery, runtime store, state repository
- `cognition/` — interface and current Ollama adapter
- `interaction/` — conversation contracts/runtime/generator
- `voice/` — contracts, integration, providers, runtime, text handling
- `embodiment/` — adapter, arbiter, avatar service, contracts, presentation
- `sensors/` — camera, consent, microphone, session
- `contracts/` — typed data contracts for cognition, conversation, decisions, events, execution, observations, sensors and World.

Do not add empty modules just to match a planned diagram. Keep interfaces small and introduce modules only when a stage requires them.

---

## 6. Research register — observed lesson and limits

### R1 — Microduck

**Primary evidence:** `microduck-main/autod/src/brain.rs`; `microduck-main/docs/design/autonomous.md` (status: draft); `microduck-main/docs/design/architecture.md` (status: draft).

**Observed lesson:** the Brain reads one World snapshot and returns a Step. The source explicitly describes it as pure decision-making that does not touch sockets; `main.rs` turns outputs into intents. It uses internal state, novelty, time, freshness and takeover/busy conditions to produce behavior.

**SYLPHY adaptation:** keep the Entity Brain deterministic and testable without model/network/filesystem access. Feed it immutable World snapshots and explicit elapsed time. Implement stale/unknown handling and yield/resume semantics where the digital environment needs them.

**Do not copy:** robot locomotion, odometry, laser/ball behavior, arbitrary randomized movement, sensor assumptions, or the multidaemon robot stack. The architecture/autonomous docs are marked draft; use the code and tests as stronger evidence than the narrative docs.

### R2 — Reachy Mini 1.15.0

**Primary evidence:** `reachy_mini-1.15.0/src/reachy_mini/apps/manager.py`; `reachy_mini-1.15.0/src/reachy_mini/daemon/robot_app_lock.py`; `reachy_mini-1.15.0/docs/source/SDK/lifecycle-contract.md`.

**Observed lesson:** daemon/app lifecycle needs one authority; managed app processes have explicit start/exit/cleanup paths; resource ownership should be scoped to the contested slot. `RobotAppLock` itself explains that it arbitrates managed local apps and remote sessions, not every possible client path.

**SYLPHY adaptation:** one Windows Entity Daemon initially; explicit process identity and child cleanup; per-resource claims for resources that truly conflict. A voice output session, model generation, file worker and avatar renderer need not block one another unless they share a concrete resource.

**Do not copy:** the robot hardware backend, motor/media assumptions, WebRTC robot-session model, or high-frequency control loops. SYLPHY is a digital companion, not a robot.

### R3 — Animama

**Primary evidence:** `animama-main/README.md`, `animama-main/SPECS.md`, `animama-main/PLANS.md`, `animama-main/SDLC.md`; `animama-main/docs/adr/0009-shared-user-memory-and-per-anima-relationship-memory.md`; `animama-main/docs/backend/features/memory-manager-and-episodic-memory.md`; `animama-main/.agents/skills/animama-vrm/`, `animama-main/.agents/skills/animama-animation/`, `animama-main/.agents/skills/animama-model-benchmark/` and `animama-main/.agents/skills/animama-local-stacks/`.

**Observed lesson:** distinguish a companion's identity from user facts and relationship-specific memory; separate raw episodic captures from curated memory; use explicit approvals, version guards, history and undo for memory edits; define avatar animation through an explicit clip contract and validate against the actual avatar/runtime; write project plans with scope, non-goals, acceptance criteria and review gates.

**Important qualification:** Animama's archive is a substantial product with multiple runtimes and memory workflows. Its ADR 0009 notes that the rejection of automatic background consolidation was superseded for shared user memory by a later ADR; do not quote one ADR as the final current policy without checking later decisions. Local model support in this snapshot is described as experimental. Voice/avatar feature counts are not evidence of suitable Vietnamese quality or SYLPHY requirements.

**SYLPHY adaptation:** keep identity, user profile, episodic evidence and curated memory conceptually separate; maintain provenance and reversible corrections; benchmark local inference and audio quality on the actual target machine; define a renderer-neutral expression contract before overbuilding embodiment.

**Do not copy:** Animama's full emotion catalog, all its avatar effects, entire product architecture, cloud integrations, or an automatic memory manager just because they exist there.

### R4 — Mark-LVII

**Primary evidence:** `Mark-LVII-main/core/outcome.py`; `Mark-LVII-main/core/confirm.py`; `Mark-LVII-main/core/tasks/engine.py`; `Mark-LVII-main/core/tasks/store.py`; `Mark-LVII-main/core/llm_client.py`; `Mark-LVII-main/AGENTS.md`.

**Observed lesson:** background work should have bounded states, round/time budgets, checkpoint/resume/cancel semantics and storage separate from UI; irreversible operations require a UI-originated confirmation token; outcomes distinguish `done`, `unverified`, `failed`, `ask` and `started`; “done” requires observing the side effect, not merely receiving a successful-looking return value. The LLM client demonstrates an OpenAI-compatible endpoint option, but not SYLPHY's runtime correctness.

**SYLPHY adaptation:** when scheduled/background tasks are eventually introduced, model their lifecycle and outcome truth explicitly; keep confirmation authority in the application/UI, not in model text; require evidence after side effects.

**Do not copy:** broad tool execution, unrestricted desktop control, cloud-provider assumptions, or Mark-LVII's complete agent/action architecture. Its compatibility with a llama.cpp server does not prove that SYLPHY's provider integration works.

### R5 — Roamies

**Primary evidence:** `Roamies-main/README.md`; `Roamies-main/PRIVACY.md`; `Roamies-main/SECURITY.md`; `Roamies-main/AGENTS.md`; relevant safety implementation under `Roamies-main/showcase/tauri/sidecar/`.

**Observed lesson:** desktop companions need an explicit disclosure of what data leaves the device; capture modes must be scoped and visible; all file/command/URL/app-control tools should pass through a centralized safety layer; risky operations need explicit switches and review. Its privacy document clearly states that selected external providers receive conversation/state and, when used, screenshots or window text.

**SYLPHY adaptation:** default to local-only processing; make any remote provider an explicit opt-in with a concrete data-flow explanation; enforce sensors and tools through application-level consent and permission checks; record what is captured and why.

**Do not copy:** Roamies' cloud subscriptions and providers as a default, always-available screen access, broad app-control features, or assumptions about its cross-platform helpers working on SYLPHY's Windows target.

---

## 7. Decision traceability — why each real change belongs in SYLPHY

Each decision below is either already present and worth preserving, a concrete gap found in the archive, or a proposed later-stage improvement. Do not implement every line blindly: follow the stage gates.

### D01 — Preserve one Entity lifecycle authority

- **Problem:** multiple services or UI components independently owning startup/shutdown creates duplicate state, orphan work and conflicting ownership.
- **Evidence:** Reachy Mini's AppManager and lifecycle contract show managed app lifecycle/cleanup; the SYLPHY master plan already defines one Entity Daemon and a single runtime authority.
- **Lesson:** one component owns process lifecycle; feature modules expose controlled capabilities rather than starting independent systems.
- **SYLPHY change:** preserve the existing `sylphy_entity.runtime` and `daemon.py` ownership. Add no new daemon unless a measured isolation/security need exists.
- **Current status:** DOC-CLAIM says Stage 1 was Windows-verified; source contains runtime/lifecycle modules. Revalidate the current archive after Stage 0.
- **Verification:** single-instance test, clean shutdown, restart/boot continuity, no orphan worker process, daemon health and state ownership tests.
- **Do not copy:** Reachy's multiple hardware services wholesale.

### D02 — Keep Brain pure and model-independent

- **Problem:** a Brain that calls the model or performs I/O becomes slow, nondeterministic and difficult to stress-test.
- **Evidence:** `microduck-main/autod/src/brain.rs` explicitly describes a pure function over `World` that returns `Step`, with intent execution outside the Brain; SYLPHY already has `brain/core.py`, `simulation.py`, `transitions.py`.
- **Lesson:** decision core is deterministic; side effects occur downstream.
- **SYLPHY change:** preserve Brain purity; prohibit LLM, filesystem, HTTP, SQLite and worker imports in the Brain package. LLM proposals must return through typed contracts and deterministic policy.
- **Verification:** architecture import test, deterministic replay, seeded stress test, stale/unknown cases, takeover/yield/resume cases.
- **Do not copy:** robot-specific drives, maps or motor action.

### D03 — Make observation freshness explicit

- **Problem:** old or missing observations can be mistaken for current facts.
- **Evidence:** Microduck's World distinguishes missing/off/lost/unsure/known map states and uses sensor freshness; SYLPHY World and observation contracts already define typed representations.
- **Lesson:** “not observed,” “unknown,” “stale,” “inferred,” and “confirmed” are distinct states.
- **SYLPHY change:** preserve freshness and provenance fields in World facts; every observation has source and time; stale data expires or is downgraded instead of silently remaining current.
- **Verification:** freshness clock tests, missing sensor tests, stale restoration after restart, contradictory observation tests.
- **Do not copy:** robot map/odometry semantics.

### D04 — Preserve the intent / permission / execution / outcome boundary

- **Problem:** an LLM proposal or an accepted request can be mistaken for an authorized, completed action.
- **Evidence:** SYLPHY `CRI_001_COMPANION_RUNTIME_CONTRACT.md` defines event → context → decision → policy → action → outcome → state update and says denied decisions cannot cause actions; Mark-LVII `core/outcome.py` distinguishes completion from unverified or failed outcomes.
- **Lesson:** proposals, authorization, execution and verified outcome are separate states.
- **SYLPHY change:** keep Brain/cognition output as proposals. Runtime validates, checks policy, executes via capability workers, then verifies a real result before recording success.
- **Current status:** Entity `execution/` and `contracts/` exist; CRI-001 describes the legacy companion contract, not proof the Entity path fully implements it.
- **Verification:** denied action never reaches worker; success requires postcondition evidence; failure and unknown outcomes persist distinctly; model cannot self-approve.
- **Do not copy:** Mark-LVII's task/action features beyond this reliability pattern.

### D05 — Use resource-specific arbitration, not a universal busy lock

- **Problem:** a single global lock blocks independent work and obscures the actual contention.
- **Evidence:** Reachy's `robot_app_lock.py` arbitrates a particular managed app/remote-session slot, and its comments explicitly scope the lock's coverage.
- **Lesson:** arbitrate only resources that actually conflict, with clear acquisition, release and cancellation rules.
- **SYLPHY change:** preserve `execution/resource_arbiter.py` and `embodiment/arbiter.py` as separate resource ownership boundaries; add explicit claims only for real contention.
- **Verification:** two independent operations run concurrently; same-resource conflict is serialized or rejected; cancellation/restart releases claims safely.
- **Do not copy:** a single Reachy app slot as a universal system lock.

### D06 — Persistence and recovery belong to the Entity, not the UI

- **Problem:** transient UI queues or process memory lose work and make restart behavior dishonest.
- **Evidence:** SYLPHY's Entity persistence package and CRI-003 continuity document describe durable state/recovery; Mark-LVII `core/tasks/store.py` demonstrates atomic file replacement and separate task metadata/log storage.
- **Lesson:** durable state, checkpoints, idempotency and recovery are explicit runtime concerns; UI is a view, not the source of truth.
- **SYLPHY change:** preserve SQLite/event-store ownership in Entity; recover in-flight intent as unknown until reconciled, not automatically successful; use idempotency keys for replayed events.
- **Verification:** crash between action and outcome, restart recovery, duplicate event replay, corrupt/partial state handling, atomic transaction tests.
- **Do not copy:** Mark-LVII's storage format unless a concrete need exists; do not introduce another database.

### D07 — Keep memory provenance and correction history

- **Problem:** unreviewed or context-inferred statements can become permanent “facts”; correction/deletion without history makes memory untrustworthy.
- **Evidence:** `docs/development/G2_MEMORY_ARCHITECTURE.md` details confirmed versus candidate memory, provenance, bounded retrieval, correction history and deletion; Animama's memory manager separates episode capture from curated memory and uses approvals/version guards/undo.
- **Lesson:** episodic evidence and curated facts are different; retrieval must be bounded and provenance-bearing; memory mutation should be reversible and user-governed.
- **SYLPHY change:** preserve confirmed-only retrieval and bounded context. Keep raw episode records separate from curated user memory. Every memory has origin, timestamp, confidence/status, version and deletion/correction history. Define any automatic consolidation as a separately gated feature, not a default.
- **Current blocker:** Entity memory source package absent from S0 archive; cannot independently audit/execute the memory path until restored from a matching source snapshot.
- **Verification:** unconfirmed candidate is not recalled as fact; user correction creates an auditable revision; delete/revert behaves correctly; context budget is enforced before prompt assembly; source references are preserved.
- **Do not copy:** Animama's full multi-Anima workspace or automatic episode processing policy without explicit SYLPHY decision.

### D08 — Make identity an executable runtime authority, not a decorative file

- **Problem:** an identity file can exist in the repository while the actual conversation code ignores it.
- **Evidence:** S0 has `identity.toml` and `config.toml` with `identity_file = "identity.toml"`; the inspected `sylphy_entity/interaction/conversation_runtime.py` constructs a hard-coded English system prompt in `_respond()` and imports memory recall, but no Entity-package identity loader/reference was found. The legacy `src/sylphy/identity.py` has separate loading behavior; this does not prove Entity wiring.
- **Lesson:** source-of-truth must be connected to the live runtime and tested. Identity is not the same as security policy or model capability.
- **SYLPHY change:** implement a small, typed identity loader/service for `sylphy_entity`; inject it into conversation/context construction; remove duplicated hard-coded personality prompt; validate missing/invalid identity behavior; keep policy and permissions application-enforced outside prompt text.
- **Status:** PROPOSED high-priority gap. Do not claim fixed until tests prove the runtime actually consumes the configured identity file.
- **Verification:** change a test identity file and assert prompt changes; ensure policy boundaries cannot be overridden by identity text; ensure provider switch does not change stable identity; missing file is handled explicitly.
- **Do not copy:** an LLM-only “soul prompt” as the whole safety system.

### D09 — Silence and initiative must be policy outcomes

- **Problem:** a companion that speaks whenever it can becomes intrusive; “proactive” is not a permission model.
- **Evidence:** SYLPHY identity says silence is valid; Soul Contract SC-03/SC-09 specify bounded initiative and appropriate silence. Legacy CRI-004/005/006 documents describe an event-bound, feature-flagged presence/autonomy pipeline, but their code references `src/sylphy`, not proof of Entity integration. Roamies/Mark also show user-controlled triggers and cooldowns in their own contexts.
- **Lesson:** an initiative trigger needs a real event, context evidence, policy decision, cooldown/expiry, delivery boundary and user control.
- **SYLPHY change:** introduce initiative only behind explicit configuration and event sources. Silence is first-class. No synthetic “user returned” or continuous monitoring without a real consented signal.
- **Verification:** no event/no initiative; denied permission produces no delivery; cooldown and expiry work; delivery success only after a real output consumer confirms it.
- **Do not copy:** generic check-in loops or idle messages that exist only to make the companion appear alive.

### D10 — Local inference backend is replaceable; llama.cpp is owner-directed

- **Problem:** backend details and identity must not be tangled into Brain or interaction policy.
- **Evidence:** SYLPHY has `cognition/interface.py` and an Ollama adapter; Animama describes multiple local stacks but marks local model operation experimental in its archive; Mark-LVII's LLM client supports OpenAI-compatible endpoints, including deployments such as llama.cpp, but that is not a SYLPHY integration test.
- **Owner direction:** the project owner has expressed a preference to move from Ollama to llama.cpp and use GGUF models. Treat this as OWNER-DIRECTED, not as a research conclusion.
- **SYLPHY change:** implement or adapt one provider behind the existing cognition interface. Keep generation out of Brain. Benchmark candidate GGUFs on the actual i7-10850H / Quadro T1000 4GB / approximately 32GB RAM machine for latency, memory, context reliability, structured output, Vietnamese quality and failure handling.
- **Verification:** same provider contract tests, timeout/cancel, malformed output, context-limit behavior, cold/warm latency, peak RAM/VRAM, offline behavior, model/version recorded.
- **Do not copy:** model names or performance claims from another project; never claim 32k or a model's quality without measured evidence.

### D11 — Voice is a separate capability with independent consent and acceptance

- **Problem:** a voice feature can fail due to language quality, latency, device routing or licensing even when text conversation works.
- **Evidence:** S0 has voice contracts/providers/runtime and config disables voice by default; Animama and Roamies use separate STT/TTS components and disclose distinct provider data flows.
- **SYLPHY change:** keep STT and TTS separate from cognition and conversation; maintain explicit enablement, device selection, interrupt/cancel behavior and a visible microphone state. Evaluate Vietnamese STT/TTS on the user's real environment before enabling by default.
- **Verification:** no microphone capture when disabled; no audio retention unless explicitly specified; end-to-end latency and Vietnamese transcription/speech quality are measured; speaker selection and shutdown are verified on Windows.
- **Do not copy:** an external speech provider or always-on wake-word behavior without explicit opt-in and privacy review.

### D12 — Embodiment must be a presentation adapter

- **Problem:** animation/VRM complexity can take ownership away from Entity behavior or make an untested avatar look more complete than it is.
- **Evidence:** S0 separates `embodiment/adapter.py`, `arbiter.py`, `avatar_service.py`, contracts and presentation; Animama's VRM/animation skills specify clip contracts, coordinate mapping, runtime ownership and live validation against the actual model.
- **SYLPHY change:** preserve the adapter boundary. Define expression/gesture intents in neutral contracts; let the renderer own frame updates. Do not let avatar state decide permissions, identity or autonomy.
- **Verification:** deterministic contract tests plus a real Windows/Electron preview using the actual avatar asset and installed renderer dependency versions; test cancellation, conflicting expressions and missing assets.
- **Do not copy:** Animama's 24 emotions, particle effects or animation library as a required baseline. No 3D/VRM completion claim while npm/TypeScript/runtime checks remain blocked.

### D13 — Sensors and desktop observations require scoped consent

- **Problem:** camera, microphone, window text and file access can expose sensitive information even if the local model never sends it to a cloud.
- **Evidence:** S0 has `sensors/consent.py`, camera/microphone/session modules; Roamies' privacy/security docs enumerate exactly what is captured, where it goes, and which actions route through safety.
- **SYLPHY change:** each sensor/capture capability gets an independent consent state, clear activation signal, scope, retention rule and stop mechanism. Keep sensors off by default. Never infer consent for screenshot, microphone, webcam, file system or remote endpoint from a different permission.
- **Verification:** disabled-state tests, consent revocation, bounded capture duration/scope, no secret logging, no unexpected network egress, evidence provenance and cleanup.
- **Do not copy:** Roamies' broad screen capture, app automation or provider data flow as default behavior.

### D14 — Add durable background tasks only after the basic Entity is trustworthy

- **Problem:** background tasks can survive the visible interaction but become uncontrolled without budgets, checkpointing and cancellation.
- **Evidence:** Mark-LVII task engine/store demonstrates explicit queued/running/waiting/done/failed/cancelled states, bounded rounds/time and resumable checkpoints.
- **SYLPHY change:** only after baseline, permissions, outcomes and persistence are verified, add a narrow scheduler/task layer with explicit owner, purpose, budget, expiry, cancel and recovery semantics.
- **Verification:** restart resumes only safe tasks; cancellation stops work; task budget is enforced; no task silently expands its capabilities; every user-facing completion is outcome-verified.
- **Do not copy:** an always-running agent loop or broad task catalog as the project's core identity.

---

## 8. Implementation status — what is known versus what is not

### 8.1 Historical stage status in S0

The in-archive master plan, updated 24 November 2026, records:

- **Stage 1 — Daemon/runtime authority:** recorded complete and Windows-verified. Treat as DOC-CLAIM; preserve and revalidate if affected.
- **Stage 2 — World/observation:** recorded complete and Windows-verified. Treat as DOC-CLAIM; preserve and revalidate if affected.
- **Stage 3 — Brain:** simulator exit criteria recorded as demonstrated. Treat as DOC-CLAIM; inspect purity and tests.
- **Stage 4 — Intent/permission/arbitration/execution:** recorded complete and Windows-verified. Treat as DOC-CLAIM; inspect capability boundaries.
- **Stage 5 — Persistence/recovery:** recorded complete and Windows-verified. Treat as DOC-CLAIM; inspect recovery and current DB compatibility.
- **Stage 6 — First autonomous loop:** recorded complete and Windows-verified. Treat as DOC-CLAIM; inspect actual source and scenario evidence.
- **Stage 7 — Cognition/memory/conversation/embodiment:** recorded in progress. Split by slice; never claim whole Stage 7 complete.

The status table is a valuable historical roadmap but cannot replace a fresh baseline. Preserve prior acceptance records; do not rerun destructive migrations on a real user database.

### 8.2 Known limits and pending checks

- The main ZIP is missing the Entity memory package (Stage 0 blocker).
- Entity conversation prompt appears hard-coded; identity file integration is not evidenced in `sylphy_entity`.
- `config.toml` selects Ollama at `127.0.0.1:11434`, model `lfm3-uncensored-32k:latest`, context budget 8192; the current code archive includes an Ollama adapter, not a verified llama.cpp adapter.
- Voice is disabled in config; speaker/device and Vietnamese quality acceptance are not established by the archive.
- Frontend build is blocked in the historical report by missing `three` and `@pixiv/three-vrm`; Electron interactive acceptance remains pending.
- R7 report's 319 tests are historical and not reproducible from S0 until the missing memory package is restored.
- Legacy CRI-004/005/006 presence/autonomy path is described under `src/sylphy`; it must not be presented as an Entity feature without independent proof.
- CRI-007 records real blockers: investigation execution lacked `BRAVE_SEARCH_API_KEY` and enabled search/reader; the actual user-facing delivery was not fully proven; an in-memory queue could lose pending items on shutdown. Do not claim an end-to-end autonomous research-and-delivery loop is verified.

---

## 9. Rebuild roadmap — phases, dependencies and exit gates

The roadmap is sequential where safety and runtime authority are concerned. Some research/prototype work can happen in parallel, but no parallel capability may bypass the Entity runtime, policy or permissions.

### Phase 0 — Archive integrity and baseline (must pass first)

**Scope:** resolve missing memory package; establish exact source snapshot, Python environment, dependencies, collected test count and grouped test results.

**Deliverables:** archive manifest; recovery provenance if applicable; baseline audit; reproducible test commands/logs.

**Exit gate:** no missing import-critical source; test collection completes; known failures are listed; owner is informed of unresolved archive defects.

### Phase 1 — Architecture and authority audit

**Scope:** map every active import path; identify which code runs in `sylphy_entity`; verify legacy isolation; audit daemon lifecycle, Brain purity, World updates, permissions, arbitration and persistence.

**Deliverables:** `docs/rebuild/ARCHITECTURE_AUDIT.md`; dependency map; list of confirmed invariants and deviations.

**Exit gate:** can explain actual event-to-outcome flow from code, not just from diagrams; no new architecture is proposed until this map exists.

### Phase 2 — Identity integration and conversation truth

**Scope:** close D08. Define typed identity configuration and load it into Entity conversation context. Separate identity content from deterministic interaction policy and permissions.

**Deliverables:** identity loader/contract; prompt assembly tests; provider-independent behavior benchmark with scenario prompts; migration note if format changes.

**Exit gate:** runtime consumes the configured identity file; missing/invalid file behavior is explicit; tests show stable identity across provider adapters; prompt cannot grant capabilities.

### Phase 3 — Cognition backend portability and model benchmark

**Scope:** retain cognition interface; implement llama.cpp OpenAI-compatible adapter only if owner still approves; benchmark current and candidate local models under resource limits.

**Deliverables:** provider adapter; benchmark procedure/results; model/runtime configuration record; failure/timeout contract.

**Exit gate:** provider switching requires config, not Brain changes; structured output and Vietnamese quality measured; resource/latency limits recorded; no unverified model capability claims.

### Phase 4 — Memory and conversation closure

**Scope:** restore/audit `sylphy_entity.memory`; verify capture, candidate approval, confirmed retrieval, provenance, correction, versioning and deletion; check context budgets and untrusted-data boundaries.

**Deliverables:** memory architecture map; repository/data migration notes; memory lifecycle test matrix; privacy/retention policy.

**Exit gate:** only confirmed memory can be stated as fact; every memory answer is traceable; corrections and deletion are auditable; memory never bypasses policy.

### Phase 5 — Outcome truth, bounded initiative and recovery

**Scope:** audit intent-to-worker postconditions, durable outcomes, idempotency and recovery; only then decide whether to integrate presence/initiative concepts from legacy CRI docs.

**Deliverables:** outcome status contract; failure-injection tests; bounded task/initiative policy ADR; delivery evidence tests.

**Exit gate:** denied action is impossible to execute; proposed/started is not called done; postcondition evidence determines success; restart does not duplicate unsafe work; silence is a valid decision.

### Phase 6 — Sensors and voice acceptance

**Scope:** camera/microphone consent, lifecycle and privacy audit; separately test STT/TTS on Windows with Vietnamese samples and actual speaker/microphone devices.

**Deliverables:** sensor consent contract; capture/retention data-flow diagram; voice quality/latency report; Windows acceptance results.

**Exit gate:** disabled sensors capture nothing; revocation stops capture; voice can be cancelled and cleanly shut down; no default network egress; quality is acceptable to owner.

### Phase 7 — Embodiment acceptance

**Scope:** avatar adapter, arbiter, asset loading, renderer/package versions, expression/gesture contracts, real preview.

**Deliverables:** renderer dependency lock; avatar asset provenance; visual regression/smoke scenarios; renderer integration notes.

**Exit gate:** actual Windows/Electron runtime renders and responds to intents; missing assets/dependencies fail clearly; avatar does not own policy or Entity lifecycle.

### Phase 8 — Bounded background tasks and release hardening

**Scope:** only introduce recurring/scheduled tasks if user has a concrete need; apply budgets, confirmation, durable checkpoints, cancel, audit and rollback.

**Deliverables:** task lifecycle ADR; threat model; recovery matrix; release/upgrade and rollback procedure.

**Exit gate:** tasks remain bounded and transparent across restart; every external side effect is authorized and verified; no hidden monitoring; installation can be diagnosed and rolled back safely.

---

## 10. Acceptance evidence matrix

Every milestone should leave a concise record with these rows. Add cases as appropriate, but do not remove safety rows to make the report look green.

- **Archive/source:** accept only after hashes, complete imports and clean extraction are recorded. Fail on missing import-critical source or mixed snapshots.
- **Runtime:** test single instance, lifecycle start/stop and child cleanup. Fail on orphan process, duplicate daemon or lost durable state.
- **Brain:** test pure imports, deterministic replay and stress cases. Fail on I/O/model dependencies or hidden side effects.
- **World:** test observation provenance and freshness. Fail if stale/unknown data is treated as a current fact.
- **Permissions:** prove denied intent never reaches worker. Fail if model or caller can bypass permission checks.
- **Arbitration:** prove independent work can proceed and shared-resource conflict is handled. Fail on universal lock or double ownership.
- **Persistence:** test crash/restart, duplicate replay and in-flight ambiguity. Fail if an action is replayed as success without reconciliation.
- **Memory:** test provenance, confirmation, bounded retrieval, correction and deletion. Fail if candidate memory is stated as fact or silently mutated.
- **Identity:** prove the configured file is actually loaded and stable across providers. Fail if a hard-coded prompt ignores configured identity.
- **Cognition:** test timeout, invalid output, model budget and provider contract. Fail if the model can execute or grant permissions.
- **Initiative:** test real event, cooldown, expiry, silence and delivery consumer. Fail on fabricated triggers or false delivery claims.
- **Sensors:** test off-by-default, scoped consent, revoke/stop and retention. Fail on capture without clear consent or scope.
- **Voice:** measure Vietnamese quality, latency, cancellation and device behavior. Fail if a provider unit test is the only evidence.
- **Avatar:** test a real Windows render, asset/package compatibility and arbitration. Fail if build checks are blocked but feature is marked complete.
- **Background tasks:** test checkpoint, budget, cancel, approval and outcome evidence. Fail on indefinite/unbounded work or silent side effects.

---

## 11. Development documentation that must accompany the code

The future assistant must keep decisions and implementation traceable. Add these documents to the repository only after inspecting the current `docs/` layout and avoiding duplicate authorities.

```text
docs/
  rebuild/
    BASELINE_AUDIT_YYYYMMDD.md
    ARCHITECTURE_AUDIT.md
    SOURCE_AND_EVIDENCE_REGISTER.md
    IMPLEMENTATION_STATUS.md
    WINDOWS_ACCEPTANCE_YYYYMMDD.md
  research/
    MICRODUCK_FINDINGS.md
    REACHY_MINI_FINDINGS.md
    ANIMAMA_FINDINGS.md
    MARK_LVII_FINDINGS.md
    ROAMIES_FINDINGS.md
  adr/
    ADR-0001-entity-authority.md
    ADR-0002-brain-purity-and-world-evidence.md
    ADR-0003-intent-permission-outcome.md
    ADR-0004-resource-arbitration.md
    ADR-0005-memory-provenance-and-lifecycle.md
    ADR-0006-identity-runtime-integration.md
    ADR-0007-inference-provider-portability.md
    ADR-0008-sensor-and-voice-consent.md
    ADR-0009-embodiment-boundary.md
  stages/
    PHASE-0-ARCHIVE-BASELINE.md
    PHASE-1-ARCHITECTURE-AUDIT.md
    ...
```

Each ADR must include: status; context/problem; exact evidence and file paths; alternatives; decision; non-goals; consequences; implementation paths; tests; and what would cause the decision to be revisited.

Each stage record must include: objective; in-scope/out-of-scope; prerequisites; exact files changed; commands run; test results; runtime results; unresolved defects; acceptance decision; and next stage gate.

Do not create a second master plan in a different folder. This document governs until the owner approves a successor and archives this version.

---

## 12. First session runbook for the future assistant

The first session should follow this sequence exactly. It is designed to bootstrap the project without relying on memory.

### Step 1 — establish the inputs

List the files actually attached. Match the source names and hashes to Section 2. Tell the owner if one archive is missing or has a different hash. Never pretend to have inspected a missing file.

### Step 2 — read SYLPHY's own governing documents

Read, in this order:

1. `Sylphy-main/SYLPHY_ENTITY_REWORK_MASTER_PLAN.md`
2. `Sylphy-main/identity.toml`
3. `Sylphy-main/docs/identity/SOUL_PROVENANCE.md`
4. `Sylphy-main/docs/identity/soul_contract.md`
5. `Sylphy-main/docs/development/CRI_001_COMPANION_RUNTIME_CONTRACT.md`
6. `Sylphy-main/docs/development/CRI_003_CONTINUITY_AND_STATE_RECOVERY.md`
7. `Sylphy-main/docs/development/CRI_004_PRESENCE_EVENT_LOOP.md`
8. `Sylphy-main/docs/development/CRI_005_BOUNDED_AUTONOMY.md`
9. `Sylphy-main/docs/development/CRI_006_PURPOSEFUL_INITIATIVE.md`
10. `Sylphy-main/docs/development/CRI_007_REALITY_VERIFICATION.md`
11. `Sylphy-main/docs/development/G2_MEMORY_ARCHITECTURE.md`
12. `Sylphy-main/docs/entity_rework/STAGE_7_R7_COMPLETION_REPORT.md`
13. `Sylphy-main/R7_AUTOMATED_TEST_RESULTS_20261125.txt`

For items 5–10, confirm which package the document describes. Several CRI docs refer to the legacy `src/sylphy`; do not transpose their status to `sylphy_entity`.

### Step 3 — inspect the actual imports and runtime path

Trace:

- `src/sylphy_entity/daemon.py` → runtime lifecycle/supervisor;
- `runtime/` → event loop and lifecycle;
- `world/` and `contracts/observation.py` → observation ingestion and World projection;
- `brain/` → pure decision path;
- `execution/` → permission, intent router, resource arbiter and worker executor;
- `persistence/` → event store, runtime state and recovery;
- `cognition/` → current provider interface and Ollama adapter;
- `interaction/conversation_runtime.py` → identity prompt assembly and memory use;
- `memory/` → only after confirming the package exists;
- `voice/`, `sensors/`, `embodiment/` → capabilities and consent boundaries.

Draw the real call graph. An architecture diagram in a document is not a substitute for following imports and calls.

### Step 4 — finish Stage 0 before proposing code edits

Run the missing-package check, environment setup, compile and test collection. If the memory package is absent, stop and ask for the matching source recovery. Do not “fix” test collection by deleting imports or skipping tests.

### Step 5 — publish a concise but factual baseline

Give the owner:

- what is structurally present;
- what can be imported and tested;
- what tests pass/fail/are blocked;
- which status claims are only historical;
- the top 3–5 actual defects or contradictions;
- the next smallest change with an acceptance test.

### Step 6 — obtain approval only where required

No approval is needed to inspect source and run non-destructive tests. Owner approval is required before destructive database/schema migration, overwriting or deleting user data, enabling sensors/network providers, installing new system services, changing model-download/provider policy, or making architecture changes with wide consequences.

---

## 13. Handover prompt — copy this to start the future session

> You are taking over SYLPHY from zero prior memory. Read `SYLPHY_MASTER_PLAN_FUTURE_HANDOVER_v3.0.md` completely before acting. The project is a Windows-first, local-first AI companion. Treat `Sylphy-main/SYLPHY_ENTITY_REWORK_MASTER_PLAN.md` as the historical roadmap and `src/sylphy_entity/` as the intended rebuild target; `src/sylphy/` is legacy reference and must not become a dependency.
>
> Your first task is Stage 0, not coding. Verify all attached archive names and SHA-256 hashes against Section 2. Inspect the main SYLPHY ZIP for `src/sylphy_entity/memory/`. The reviewed archive is missing `__init__.py`, `repository.py`, `recall.py`, and `legacy_import.py`, although Entity conversation and memory tests import that package. If the same defect exists, stop and request a matching source recovery patch or complete archive. Do not invent the missing code, delete the tests, or claim the historical 319-test report is reproducible.
>
> Distinguish legacy CRI documentation under `src/sylphy` from behavior actually integrated in `src/sylphy_entity`. Verify identity integration: the archive contains `identity.toml` and config points to it, but the inspected Entity conversation runtime builds a hard-coded prompt and no Entity identity loader was found. Treat that as a real high-priority gap until tests prove it fixed.
>
> Preserve the sound Entity architecture: one lifecycle authority, pure deterministic Brain, typed World observations with freshness/provenance, Intent → permission → execution → evidence-backed outcome, resource-scoped arbitration, Entity-owned persistence/recovery, and replaceable cognition provider. Use Microduck, Reachy Mini, Animama, Mark-LVII and Roamies only for the specific lessons recorded in this plan; do not copy whole architectures or add features for their own sake.
>
> For every proposed change, show the chain: problem, exact source evidence, lesson, SYLPHY adaptation, implementation files, tests, observed result, limitation. Mark each claim CODE-VERIFIED, TEST-VERIFIED, RUNTIME-VERIFIED, DOC-CLAIM, RESEARCH-OBSERVATION, OWNER-DIRECTED, ENGINEERING-INFERENCE, PROPOSED or BLOCKED. Do not report tests or device behavior as passing unless you ran them and recorded the exact command/environment/result. Keep local-first privacy and explicit user consent as hard boundaries. Start by producing `docs/rebuild/BASELINE_AUDIT_YYYYMMDD.md`; do not make code changes until the source integrity and baseline are understood.

---

## 14. Final project principle

SYLPHY should become more coherent before it becomes more capable.

The priority is not to add more agents, tools, emotions, animation, sensors or background loops. It is to make the existing Entity truthful and dependable: one owner of lifecycle, clean boundaries, evidence-aware World state, deterministic decisions, explicit permissions, verifiable outcomes, durable continuity, and an identity that is actually connected to the live runtime.

Research is useful only when it leads to a justified change. A source project is not a feature checklist. A test report is not proof if the exact source snapshot cannot reproduce it. A warm personality is not permission to intrude. A model response is not evidence that an action happened.

**The future assistant's first success is not writing code. It is understanding exactly what SYLPHY is, what the archives prove, what they do not prove, and what must be fixed first.**
