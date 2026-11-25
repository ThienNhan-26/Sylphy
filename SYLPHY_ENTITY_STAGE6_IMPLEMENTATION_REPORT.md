# SYLPHY Entity Rework — Stage 6 Implementation Report

**Status: COMPLETE — Windows acceptance passed on 2026-11-24.**

## Scope and boundaries

Stage 6 is the Master Roadmap's **First Autonomous Closed Loop (mandatory proof)**. It proves one useful Entity behaviour end-to-end in an explicitly permitted filesystem sandbox. It is not a feature-integration stage.

The first capability remains read-only metadata inspection. No file content read, edit, delete, arbitrary shell, network access, LLM cognition, conversation, long-term memory import, Vision, Voice, Avatar or UI-first work is introduced. Autonomy is opt-in and requires both `--autonomy` and an explicit `--sandbox-root`.

## End-to-end behaviour implemented

`Filesystem Observation → World Model → pure Entity Brain → inspect Goal → Intent Proposal → Intent Router → Permission Policy → path-scoped Resource Arbiter → read-only metadata Worker → verified Evidence → Goal feedback → persistent history and user-inspectable notice`.

The loop runs from the Daemon's event/tick lifecycle without a new chat message. Brain remains pure: it proposes; Runtime authorizes and dispatches. The simulator uses the same contracts, dispatcher, permissions, resource arbitration and feedback path as the live sandbox proof.

## Stage 6 hardening

- File revision fingerprinting distinguishes a duplicate watcher notification from a changed version of the same path. A repeated identical revision does not execute twice; an updated revision may produce a new inspect Goal.
- `source_observation_id` is retained from the selected World fact through `IntentProposal`, `Intent`, durable journal, autonomy history and notice. Decisions can therefore be traced to their originating observation.
- A successful execution result is persisted as evidence and applied to the exact Goal attempt. If the Daemon restarts after the result is committed but before the next Brain tick, startup reconciliation applies that matching feedback. A stale success cannot complete a later attempt.
- Incomplete/unknown Intent is never blindly replayed. A real child Daemon process crash after durable `DISPATCHING` was tested; restart recovers the Intent as `UNKNOWN` without invoking the handler again.
- Periodic filesystem reconciliation recovers a missed Watchdog wake-up.
- File disappearance or invalidated scope between observation and dispatch is rejected before successful execution.
- Authenticated pause and permission revoke/restore endpoints are available. Pause/revoke blocks new autonomous admission and cancels eligible active autonomous work. Resume cannot bypass the original explicit `--autonomy` opt-in.
- Worker crash is recorded and degrades the runtime without killing the Daemon.
- Notices remain inspectable through authenticated/local runtime status. A desktop toast/push notification adapter is deliberately not part of this stage.

## Mandatory scenario matrix

| Master Plan scenario | Container verification |
|---|---|
| Create | PASS — live and simulator |
| Edit | PASS — same path, new revision |
| Rename | PASS — old path becomes unknown; new path inspected |
| Delete | PASS — no inspection success; fact becomes unknown |
| Duplicate watcher events | PASS — revision deduplication |
| Event loss + reconciliation | PASS — missed wake-up recovered |
| File disappears before read/dispatch | PASS — rejected, no success evidence |
| Permission revoked | PASS — before and during active execution |
| User interruption | PASS — authenticated pause cancels autonomous work |
| Model server offline | PASS — loop does not depend on model service |
| Worker crash | PASS — failed worker recorded; Daemon survives degraded |
| Daemon crash after dispatch | PASS — real process crash; Intent recovered UNKNOWN; no replay |
| Restart with incomplete Intent | PASS — UNKNOWN/INTERRUPTED, not automatically re-dispatched |
| Idle/no-op period | PASS — no repeated action |
| Full simulator closed loop | PASS — includes dispatch, worker, evidence and Goal feedback |
| Inspectability | PASS — Observation provenance, decision reason, Intent, result and evidence IDs are retained |

## Verification evidence

- Container implementation verification: Stage 6 focused suite and complete Entity suite passed as recorded above.
- User Windows acceptance on 2026-11-24 with Python **3.13.17**: compile check passed; mandatory Stage 6 scenario matrix **17 passed**; complete Entity test suite **167 passed, 2 skipped**.
- Final script marker observed: `PASS: Stage 6 autonomous closed-loop Windows acceptance completed`.

The Windows acceptance gate is satisfied. The two skipped tests remain reported as skipped, not failures.

## Windows acceptance

The user completed this acceptance on 2026-11-24. The following is the reproducible command for future regression checks, from the `sylphy_work` repository root using Python 3.13:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\tools\verify_entity_stage6_windows.ps1
```

The script runs compile checks, all three Stage 6 focused test files and the complete Entity test suite. Stage 6 becomes accepted only when the final Windows script reports:

```text
PASS: Stage 6 autonomous closed-loop Windows acceptance completed
```

## Roadmap boundary

- Stages 1–5 remain complete within their previously accepted boundaries.
- Stage 6 is COMPLETE — Windows verified.
- Stage 7 is IN PROGRESS with Local LLM Cognition. Live Windows + Ollama acceptance is the next gate; Memory and later capabilities remain locked until it passes.
