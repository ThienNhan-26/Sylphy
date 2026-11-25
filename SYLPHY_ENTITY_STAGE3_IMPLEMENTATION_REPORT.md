# SYLPHY Entity Rework — Stage 3 Implementation Report

**Status: COMPLETE — pure Brain simulator exit criteria demonstrated; independent audit refreshed 2026-11-24.**

## Delivered

### Brain decision core

- `EntityBrain.step(dt, world_snapshot, brain_state, ...) -> DecisionFrame` is deterministic and side-effect free.
- Brain code has no imports or calls for filesystem, operating-system I/O, database, HTTP/network, subprocess, LLM, UI, Runtime or executor services. An AST architecture-boundary test enforces this rule.
- Immutable contracts live in `contracts/decision.py`: behaviour, drive state/inputs, goals, goal feedback, goal proposals, intent proposals, brain state and decision frame. `WorldSnapshot` remains the immutable World input.
- Eight behaviour states: `RESTING`, `OBSERVING`, `EVALUATING`, `PURSUING`, `WAITING`, `REFLECTING`, `YIELDING`, `RECOVERING`.

### Decisions and goals

- Five bounded operational drives: curiosity, continuity, social responsiveness, self-maintenance and resource conservation. Inputs and decay are explicit; values remain in `[0, 1]`. These are operational variables, not human emotion claims.
- Bounded novelty history uses virtual-time recency recovery, visit frequency and inspection-depth penalties.
- Initial goal capability is deliberately limited to observing a known file change inside an explicitly scoped sandbox. The Brain never reads file contents.
- One active pursuit is selected at a time; other eligible facts remain in the World and can be evaluated after the current pursuit completes or waits.
- Goal policy considers novelty, relevance, urgency, drive pressure, resource pressure, cost, risk, source confidence and verification. `UNKNOWN` and `STALE` facts, unknown scopes, unsafe paths and facts outside a sandbox cannot create proposals.
- Goal feedback supports evidence-backed success, failure, waiting and abandonment. Failure counts increase an exponential retry cooldown; deadlines expire using virtual time; completed goals may be revisited only when novelty recovers. Retry attempts receive new Intent IDs.
- User interaction or externally owned resources yield control. Active/waiting goals suspend, deadlines pause while yielded, and the previous state resumes with transient latches reset.
- Brain returns `IntentProposal` only, including permission class, scope, required resources, target and expected outcome. It does not authorize or execute the proposal.

### World provenance correction

World facts now preserve the source ID, source observation ID, confidence, verification, permission scope and sequence needed by Brain policy. An equivalent observation may refresh provenance without inventing a semantic World version change. This prevents Brain from treating every fact as equally trustworthy.

## Verification evidence

- Independent re-run on 2026-11-24: `python -m pytest -q`: **107 passed, 2 warnings**. The warnings are upstream deprecations from `websockets.legacy` and Uvicorn's websocket protocol import; no test failed. This combined suite includes the current Stage 1–4 tests; it is not a Stage 3-only count.
- Brain subset: **36 passed** (re-run on 2026-11-24).
- `python -m compileall -q src/sylphy_entity tests/entity_brain tests/entity_runtime tests/entity_world`: passed.
- Deterministic long simulation: 1,000 ticks / 5,000 virtual seconds; replay from identical initial state/input produced equal final state and frame trace. Includes a user takeover interval and preserves the active goal through yield/resume.
- Seeded mixed-world invariant stress test: **5,000 ticks** with known/stale/unknown facts, varying risk/cost/relevance, resource pressure, user takeover and success/failure/waiting feedback. It verifies bounded drives, at most one ACTIVE goal, bounded goal history, no intents during yield, sandbox-scoped intents and safe relative targets.
- Worker failure + Daemon shutdown integration test: **8/8** separate process-level repetitions after eliminating the secondary-observer race.
- Brain architecture boundary: AST test confirms no OS I/O, subprocess, network, persistence, model or UI imports.

Two deprecation warnings originate from the currently installed Uvicorn/websockets combination. Ruff and mypy were not run because neither executable is installed in this container; no lint/type-check pass is claimed.

## Boundaries intentionally preserved

- Brain is not yet attached to the Daemon's timed tick. Stage 3 proves the pure core in simulation; wiring Brain into the lifecycle is not expanded into a new scope here.
- `IntentProposal` is not an executable action. Authorization, path-scoped arbitration and execution belong to Stage 4.
- Brain state, goals and novelty are not yet persisted across Daemon restarts. Durable continuity and reconciliation belong to Stage 5.
- No Ollama/LLM, Memory, Conversation, Vision, Voice, Avatar or UI integration. These remain Stage 7.
- No autonomous live filesystem action has been performed. The Stage 6 closed-loop proof remains locked until preceding stages pass.

## Source files

- `src/sylphy_entity/contracts/decision.py`
- `src/sylphy_entity/contracts/world.py` (runtime context and provenance fields)
- `src/sylphy_entity/world/model.py` (provenance projection)
- `src/sylphy_entity/brain/{core,drives,novelty,transitions,simulation}.py`
- `src/sylphy_entity/runtime/{process_manager,supervisor}.py` (worker supervision race fix)
- `tests/entity_brain/`
- `tests/entity_runtime/test_process_manager.py`
- `pyproject.toml` (Brain tests included in the default test suite)

**Acceptance decision:** Stage 3 exit criteria are met within the stage's specified simulator scope. The next permitted stage is Stage 4 — Intent, Permission, Resource Arbitration and Execution.
