# Stage 7 — Phase 3: Avatar Contract & Behavior Arbitration

## Repository audit

The uploaded `sylphy_work_latest(1).zip` already contained an initial Phase 3 implementation:
- `src/sylphy_entity/embodiment/contracts.py`
- `src/sylphy_entity/embodiment/adapter.py`
- `src/sylphy_entity/embodiment/arbiter.py`
- `src/sylphy_entity/embodiment/presentation.py`
- `tests/entity_embodiment/`

The existing adapter maps the actual Entity event names found in the repository, including `cognition.requested`, `cognition.result`, `conversation.message.received`, and the `voice.playback.*` events. Voice events are published through the existing Entity event ingress; no second event bus is introduced.

## Hardening changes in this patch

1. **Pause/resume preservation:** `entity.paused` now overlays a full-region PAUSED claim without deleting valid underlying claims. `entity.resumed` removes that overlay and restores whichever behavior is still valid. If Voice was interrupted while paused, its interruption event removes the old speaking claim before resume.
2. **Per-session sequence guard:** every `voice.*` event advances the session sequence watermark. Delayed/duplicate lower-sequence events can no longer release a newer speaking claim.
3. **Terminal shutdown:** after `entity.stopped`, late events and direct claims are ignored by that arbiter instance.
4. **Transient reaction cleanup:** a new cognition request or voice playback removes stale conversation reaction claims so they cannot reappear after speech ends.
5. **Regression tests:** tests cover pause restoration, stale voice sequence, progress sequence watermark, terminal shutdown, and reaction cleanup. The previous pause test expectation was updated to reflect restoration semantics.

## Priority policy

The repository's current configuration is:
- IDLE 10
- PROCESSING 30
- REACTING 50
- SPEAKING 70
- PAUSED 100
- SHUTDOWN 110

These are implementation defaults for deterministic tests, not empirically calibrated values. PAUSED is an overlay state and SHUTDOWN is terminal for the arbiter instance.

## Local verification performed

Python 3.13.5:
```text
pytest tests/entity_embodiment -q
26 passed in 0.11s
```

This is a local test result against the extracted uploaded source tree. It is not Windows acceptance evidence. Run the Windows commands below before marking Phase 3 accepted.

## Windows acceptance

From the repository root, with its Python 3.13 virtual environment activated:
```powershell
$env:PYTHONPATH = "src"
python -m pytest -q tests/entity_embodiment
python -m pytest -q tests/entity_voice tests/entity_conversation tests/entity_cognition tests/entity_memory
```

Expected for the first command: 26 passed. The second command is a regression gate; report its actual result.

## Boundaries

- No webcam Vision, microphone capture, STT, or voice input.
- No direct LLM-to-animation command path.
- No new event bus.
- No VRM renderer integration in this phase.
- Phase 4 still needs concrete VRM animation clips, bone mapping, transition/crossfade behavior, and integration of behavior commands with the renderer.
