# Stage 7 — R2.2 Presentation Runtime Interface

## Delivered

- `presentation.py` defines renderer-neutral `AnimationCommand` values (`START`/`STOP`) with semantic clip IDs, body region, owner, priority, looping, fade durations, weight, session and sequence metadata.
- `BehaviorAnimationMapper` maps winning `ArbitrationSnapshot` region owners into semantic animation clips. The renderer is responsible for resolving semantic clip IDs to actual animation assets.
- `PresentationRuntime` diffs desired commands against active commands, stops commands whose region ownership/clip is no longer selected, avoids restarting unchanged loops, and supports `stop_all()`.
- `AnimationRenderer` is a Protocol boundary. No Three.js, VRM, Unity, desktop window, or model dependency is introduced.

## Semantic clip IDs

Initial mapping includes `idle.breathing`, `thinking.posture`, `reacting.expression`, `speaking.lipsync`, `interrupted.head_recoil`, `mouth.closed`, `paused.neutral`, and `shutdown.neutral`. These are semantic identifiers, not claims that the actual animation assets already exist.

## Runtime boundaries

- The arbiter remains the sole owner of presentation priority and per-region selection.
- The mapper translates selected claims; it does not invent or infer Entity state.
- The runtime sends commands to an injected renderer; it does not load assets or run a render loop.
- Renderer errors are allowed to propagate to the caller; process isolation/recovery belongs to the later Avatar host/runtime integration.
- No webcam Vision, microphone Listening, STT, or LLM-direct animation commands are included.

## Verification

```powershell
$env:PYTHONPATH = "src"
python -m pytest -q tests/entity_embodiment tests/entity_voice
```

Development-container result after R2.2: 29 passed. Windows acceptance is pending.

## Next

R3 will implement the VRM renderer adapter and actual asset/animation resolution. The semantic IDs above must be mapped to clips/blendshapes that are actually present in the chosen VRM/model package; missing clips must degrade safely rather than crash the Entity.
