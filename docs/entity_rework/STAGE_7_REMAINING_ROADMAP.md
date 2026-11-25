# SYLPHY Stage 7 — R7 remaining-work roadmap

This roadmap is the execution record for the supplied “phần còn lại của stage7(6).txt”. The source document's scope is authoritative: complete Cognition/Memory/Conversation integration, Voice Output and synchronization, Avatar arbitration/VRM/Desktop, lip-sync, CLI/UI, and end-to-end verification. Webcam Vision and microphone Listening are excluded.

## Execution sequence and outcome

| Phase | Required outcome | R7 source state / outcome |
|---|---|---|
| 0. Baseline | Preserve accepted Stage 6; map current APIs and regression surface | Completed; see `STAGE_7_REMAINING_BASELINE.md` |
| 1. Voice Output | Local TTS adapter, real speaker output, stop/replacement/failure lifecycle | Implemented with Kokoro ONNX + sounddevice; Windows speaker acceptance pending |
| 2. Voice–Entity sync | Actual playback start/progress/end/interruption events; Entity owns durable state | Implemented through existing EventStore/EventLoop; automated tests pass |
| 3. Behavior Arbiter | Typed events, priorities, interruption, per-region ownership | Implemented; automated tests pass |
| 4. VRM runtime | VRM 1.0 loading, behavior animation, expression and mouth controllers | Implemented; renderer tests/build require Windows/npm dependency install |
| 5. Desktop | Independent transparent, topmost Electron Avatar process and recovery | Implemented; interactive Windows acceptance pending |
| 6. Lip-sync | Mouth follows real playback clock/amplitude, closes on stop/interruption | Implemented; automated Python coverage passes; browser/Vitest and real speaker observation pending |
| 7. CLI/UI + E2E | Unified inspect/control commands and repeatable acceptance | CLI surface completed in this pass; full Windows E2E acceptance pending |

## Final operator commands

From the repository root, after installing the project:

```powershell
py -3.13 -m sylphy_entity.interface.cli status
py -3.13 -m sylphy_entity.interface.cli cognition
py -3.13 -m sylphy_entity.interface.cli memory
py -3.13 -m sylphy_entity.interface.cli voice
py -3.13 -m sylphy_entity.interface.cli diagnostics
py -3.13 -m sylphy_entity.interface.cli chat "Hello, Sylphy" --session default
py -3.13 -m sylphy_entity.interface.cli speak "Hello. I am here."
py -3.13 -m sylphy_entity.interface.cli voice-stop
py -3.13 -m sylphy_entity.interface.cli avatar presentation
py -3.13 -m sylphy_entity.interface.cli avatar start
py -3.13 -m sylphy_entity.interface.cli avatar status
py -3.13 -m sylphy_entity.interface.cli avatar restart
py -3.13 -m sylphy_entity.interface.cli avatar stop
py -3.13 -m sylphy_entity.interface.cli pause
py -3.13 -m sylphy_entity.interface.cli resume
py -3.13 -m sylphy_entity.interface.cli permission revoke
py -3.13 -m sylphy_entity.interface.cli permission restore
```

`pause` / `resume` and permission changes require the daemon's local control token; the CLI reads it from the per-user runtime directory. `avatar` process actions manage only the Avatar child process. They never start or stop the Entity Daemon.

## Acceptance rule

Automated tests and static checks are necessary but are not substitutes for target Windows evidence. R7 is an implementation-complete candidate, not a Windows-accepted release, until the supplied Windows script and manual speaker/desktop checks pass.
