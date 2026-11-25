# Stage 7 — R7 End-to-End Windows Acceptance

This is the final target-machine acceptance gate. Run it only after installing Python and npm dependencies. The automated script runs the Python Entity suite, Avatar tests/build, and Avatar process-manager status check. It does not fake audio output or desktop interaction.

## Automated gate

From repository root:

```powershell
py -3.13 -m pip install -e ".[dev,voice-output]"
Push-Location avatar
npm ci
Pop-Location
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\tools\verify_r7_windows.ps1
```

The script must exit with code 0 and print its automated PASS marker. Any npm or test failure blocks acceptance.

## Live E2E

Use a configured local Ollama model and local Kokoro ONNX model/voices assets:

```powershell
$env:PYTHONPATH = "$PWD\src"
py -3.13 -m sylphy_entity.daemon --conversation-model <ollama-model> --voice-model <kokoro-model.onnx> --voice-voices <voices.bin>
```

In a second PowerShell window:

```powershell
$env:PYTHONPATH = "$PWD\src"
py -3.13 -m sylphy_entity.interface.cli status
py -3.13 -m sylphy_entity.interface.cli cognition
py -3.13 -m sylphy_entity.interface.cli memory
py -3.13 -m sylphy_entity.interface.cli chat "Hello, Sylphy" --session r7-acceptance
py -3.13 -m sylphy_entity.interface.cli history --session r7-acceptance
py -3.13 -m sylphy_entity.interface.cli speak "Hello. I am here."
py -3.13 -m sylphy_entity.interface.cli voice
py -3.13 -m sylphy_entity.interface.cli avatar start
py -3.13 -m sylphy_entity.interface.cli avatar status
```

## Manual acceptance checklist

- [ ] Text message reaches local Ollama and a response is persisted in conversation history.
- [ ] Memory status is inspectable; retrieved memory context remains provenance-carrying and untrusted.
- [ ] Kokoro synthesizes speech locally and sounddevice plays it through the selected Windows output device.
- [ ] `voice.playback.started` is emitted only after actual output begins.
- [ ] Avatar displays speaking behavior from the Entity-owned presentation projection.
- [ ] Mouth remains closed before playback; amplitude lip-sync follows the playback clock while speaking.
- [ ] `voice-stop` interrupts output and closes the mouth.
- [ ] Normal playback completion returns Avatar to its fallback/idle behavior.
- [ ] Electron window is transparent, frameless, always-on-top, movable and resizable.
- [ ] Tray restore and configured global shortcuts work.
- [ ] Stopping Avatar does not stop Entity; restarting Avatar obtains the latest presentation revision.
- [ ] Killing Avatar process does not crash Entity; process manager can restart it.
- [ ] Closing CLI does not stop Entity.
- [ ] No camera or microphone permission is requested; no Vision, Listening or STT path is required.

## Result recording

Record date, Windows version, Python version, npm version, Ollama model, Kokoro model/voice asset versions, output device, automated script output, and each manual checklist result. Mark Stage 7 Windows acceptance PASS only when every applicable item above is observed on the target Windows machine.
