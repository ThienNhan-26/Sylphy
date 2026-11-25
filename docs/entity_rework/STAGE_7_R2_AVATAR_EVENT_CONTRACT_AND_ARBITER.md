# Stage 7 — R2 Avatar Event Contract & Behavior Arbiter

## Scope delivered

This slice creates a renderer-independent presentation contract. It does not load VRM, render a window, animate a skeleton, or change Entity's source-of-truth state.

### Modules

- `src/sylphy_entity/embodiment/contracts.py`: typed Avatar event kinds, behavior names, body regions, and behavior claims.
- `src/sylphy_entity/embodiment/adapter.py`: translates recognized authoritative `EntityEvent` records into Avatar presentation events; unknown events are ignored.
- `src/sylphy_entity/embodiment/arbiter.py`: deterministic priority arbitration and independent per-region ownership.
- `tests/entity_embodiment/`: arbitration and Entity-event adapter tests.

## Behavior model

| Behavior | Default priority | Meaning |
|---|---:|---|
| IDLE | 10 | Fallback when no active claims exist; implicit, not an override claim |
| PROCESSING | 30 | Cognition request active |
| REACTING | 50 | Conversation message received |
| SPEAKING | 70 | Voice playback active |
| INTERRUPTED | 90 | Voice playback interrupted |
| PAUSED | 100 | Entity paused |
| SHUTDOWN | 110 | Entity stopped |

These values are initial policy defaults for deterministic arbitration, not empirically validated animation priorities. They are injectable/configurable in tests and can be tuned after runtime observation.

## Region ownership

The initial region partition is BODY, HEAD, EYES, FACE, MOUTH. A claim may own one or more regions. The arbiter selects a winner independently for each region using priority, then sequence, then owner as deterministic tie-breakers. This prevents concurrent behaviors from issuing conflicting animation control to the same region.

## Architectural boundaries

- EntityEvent remains authoritative; AvatarEvent is a presentation projection.
- Avatar does not mutate World Model, Brain, Memory, Conversation, or Voice state.
- The arbiter only chooses presentation claims; it does not execute animation clips.
- No LLM output is accepted as a direct animation command.
- There is no LISTENING behavior and no camera/microphone event requirement.
- Entity pause/shutdown can preempt all presentation claims. Resume releases the pause claim; new activity releases transient interrupted behavior.

## Verification

```powershell
$env:PYTHONPATH = "src"
python -m pytest -q tests/entity_embodiment tests/entity_voice
```

Development-container result: 19 passed. Windows acceptance is still required after patch application.

## Next slice

R2.2 should connect the arbiter to a presentation runtime interface and define animation command outputs without binding it to Three.js/VRM. R3 can then implement the VRM renderer and consume those outputs.
