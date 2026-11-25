# Stage 7 R1 — Entity Voice Integration

## What this slice adds

- `VoiceCapability` bridges `VoiceEvent` facts into `EntitySupervisor.publish_event()`.
- The bridge converts each voice lifecycle event to the existing `EntityEvent` envelope and uses the existing durable EventStore + EntityEventLoop. No second event bus is created.
- Entity local API exposes a read-only loopback `GET /voice/status` projection (no control token required) and token-guarded `POST /voice/speak` / `POST /voice/stop` operations.
- Daemon accepts `--voice-model` and `--voice-voices` together to opt into local Kokoro output. Without both flags, Voice remains disabled and Entity starts normally.
- Voice is stopped before the Entity event loop during graceful shutdown, allowing final voice lifecycle facts to enter the normal event path.
- `/status` exposes Voice capability status without exposing speech text or model data.

## Windows setup

Activate the Python 3.13 virtual environment already used for the Entity project, then:

```powershell
python -m pip install -e ".[dev,voice-output]"
$env:PYTHONPATH = "src"
python -m pytest -q tests/entity_voice
```

## Start with Voice Output enabled

Supply the actual local Kokoro ONNX model and voices file paths. Do not guess these paths; use the locations where the user has installed the model assets.

```powershell
python -m sylphy_entity.daemon --voice-model "D:\\path\\kokoro-v1.0.onnx" --voice-voices "D:\\path\\voices-v1.0.bin"
```

To start without Voice, omit both flags. The Entity daemon does not require the Voice extra for ordinary startup.

## Read-only and authenticated API examples

`GET /voice/status` is read-only and requires no control token. Read the control token only for the mutating Voice operations: from the daemon's runtime directory (default location is defined by `default_runtime_dir()`):

```powershell
$token = Get-Content "$env:LOCALAPPDATA\\SYLPHY\\runtime\\control.token" -Raw
$headers = @{ "X-Sylphy-Control-Token" = $token.Trim() }
Invoke-RestMethod http://127.0.0.1:8765/voice/status

$body = @{
  text = "Xin chào. Tôi là Sylphy."
  voice = "af_heart"
  language = "en-us"
  speed = 1.0
} | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8765/voice/speak -Headers $headers -ContentType "application/json" -Body $body
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8765/voice/stop -Headers $headers
```

The `language` and voice identifiers must match the installed Kokoro model/voice pack. This example is only a wiring smoke test; Vietnamese phoneme quality is not claimed until tested with the selected model assets.

## Tests and acceptance boundary

- Voice unit + Entity bridge tests: 8 passed in the development container.
- Changed Python modules compile successfully.
- A combined broader Entity runtime/conversation test invocation exceeded the container's 60-second execution limit; it is not reported as PASS or FAIL.
- Real Windows speaker playback, actual Kokoro asset compatibility, daemon/API smoke test, and graceful shutdown on Windows remain required acceptance steps.
- Camera and microphone adapters are not started or referenced by this integration. There is no Listening/STT or webcam Vision path.
