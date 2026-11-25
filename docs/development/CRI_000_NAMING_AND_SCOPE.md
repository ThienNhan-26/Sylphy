# CRI-000 — Naming and Scope Decision

**Date:** 2026-11-22  
**Status:** Accepted for Turn 2 foundation.

## 1. Problem

The repository already uses the G-generation labels for existing milestones and modules:

| Existing label | Existing meaning | Evidence |
|---|---|---|
| G0 | Baseline and source control | `docs/development/BASELINE.md`, `REFERENCE_DECISIONS.md` |
| G1 | Identity and conversation verification | `docs/development/G1_IDENTITY_CONVERSATION.md` |
| G2 | Memory architecture and implementation | `docs/development/G2_MEMORY_ARCHITECTURE.md` |
| G3 | Memory table / persistent memory (in README) | `README.md` lines 21, 31 |
| G4 | Runtime presence core | `README.md` lines 1, 72 |
| G5 | Cognition candidates | `README.md` line 43 |
| G6 | Presence and exploration | `README.md` lines 78, 90 |
| G7 | Desktop avatar foundation | `README.md` line 104 |

The Turn 1 audit also referred to a proposed "G3" companion-integration program. Using "G3" for both persistent memory and a new companion-runtime initiative would create an ambiguous scope and mislabeled tests.

## 2. Decision

The new work is named:

> **Companion Runtime Initiative (CRI)**

The naming scheme is:

| Item | Convention |
|---|---|
| Initiative name | `Companion Runtime Initiative (CRI)` |
| Development documents | `docs/development/CRI_###_<topic>.md` |
| Contract module | `src/sylphy/companion_contract.py` |
| Contract tests | `tests/test_companion_contract.py` |
| Future milestone IDs | `CRI-0`, `CRI-1`, `CRI-2`, ... |

This decision **does not rename or renumber** any existing G0–G7 module, document, test, API, or historical milestone. Existing labels remain valid for their existing scope.

## 3. Scope of CRI-0 (Turn 2)

CRI-0 establishes only:

1. A version-controlled soul source and provenance record.
2. A source-derived Soul Contract.
3. A naming decision that avoids the G3 collision.
4. Typed Companion Runtime contract primitives.
5. Deterministic tests for those primitives.
6. A written specification for the first future vertical slice.

CRI-0 does **not** implement the Companion Runtime loop, an event bus, a scheduler, a worker, an autonomous task system, a new memory database, a new identity engine, an emotion engine, or a VRM/avatar framework.

## 4. Consequences

- Future documents use `CRI-###` and do not claim to be "G3".
- Existing G3–G7 documentation remains historically accurate and is not globally renamed.
- The first integration slice is specified in `CRI_002_VERTICAL_SLICE_CONTRACT.md` and is not implemented in this turn.
