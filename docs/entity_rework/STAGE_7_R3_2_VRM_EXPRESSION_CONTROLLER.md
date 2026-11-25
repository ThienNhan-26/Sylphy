# Stage 7 — R3.2 VRM Expression Controller

## Scope

R3.2 adds local, renderer-owned expression control to the isolated Avatar viewer. It does not connect the Entity daemon, interpret LLM text, or implement playback-clock lip-sync.

## Implemented

- `avatar/src/expression-controller.ts` manages available VRM expressions through a small adapter interface.
- Mood channel: happy, angry, sad, relaxed, surprised. Selecting a mood fades other mood presets out.
- Blink channel: independent from mood.
- Viseme channel: aa, ih, ou, ee, oh; only one viseme is active at a time.
- `update(deltaSeconds)` advances fades from the render-loop delta, not wall-clock time.
- Missing optional expressions are ignored safely.
- Viewer exposes `setMood`, `setBlink`, `setViseme`, and `resetExpressions`.
- Preview UI adds manual mood, blink and clear controls. These controls only affect local presentation.
- Vitest coverage includes initialization, fades, exclusivity, independent channels, missing presets, clear and invalid inputs.

## Windows verification

From `avatar/`:

```powershell
npm test
npm run build
npm run dev
```

The expression buttons become available after loading a VRM with an expression manager. Actual visual behavior depends on the expressions embedded in that VRM. A model lacking a particular preset will not synthesize it.

## Important boundaries

- No LLM-to-expression direct command path.
- No Entity IPC or event bridge yet.
- No camera, microphone, STT or webcam Vision.
- No voice playback lip-sync; viseme API is a controlled primitive for later R4 playback-clock integration.
- No animation clips or humanoid bone motion yet.

## Acceptance

Source and unit tests are authored. This build environment does not provide a verified Windows npm test/build result; Windows acceptance must be recorded from the user's machine.
