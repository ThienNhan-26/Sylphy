# Stage 7 — Conversation Slice 3A Implementation Report

**Status:** Implemented; container verification passed; Windows acceptance pending.

## Scope

Slice 3A implements only the first Conversation boundary required by the Master Plan: one user message becomes one durable, transport-neutral Entity Event. Conversation remains an interaction mode and does not own the Entity lifecycle.

## Added components

- `contracts/conversation.py`: immutable `ConversationMessage` with bounded text (16,000 characters), session/message identifiers, channel and timezone-aware occurrence timestamp.
- `interaction/conversation.py`: `ConversationIngress`, which converts one user message into one `conversation.message.received` `EntityEvent` and publishes it through the existing Runtime event-publisher boundary.
- `tests/entity_conversation/test_conversation_ingress.py`: validation, event mapping, no-reply/no-Cognition boundary, publisher failure propagation, durable EventStore journaling and idempotency checks.
- `tools/verify_entity_stage7_conversation_slice3a_windows.ps1`: Python 3.13 compile and focused Conversation/EventStore acceptance script.

## Invariants

1. The ingress accepts user messages only; role is fixed to `user` by the adapter.
2. The message ID is the Entity event ID, giving stable idempotency at the existing EventStore boundary.
3. A successful submit means the existing publisher accepted the event; it does not mean a reply was generated.
4. The adapter does not call Cognition, retrieve Memory, mutate Brain state, create Intents, or own a loop/session lifecycle.
5. The Entity Daemon remains the lifecycle authority. The existing EventStore and EventLoop remain the only event durability/dispatch path.
6. Message text is not copied into new diagnostic logs by this adapter.

## Verification

Container verification:

```text
compileall: PASS
pytest tests/entity_conversation tests/entity_persistence/test_event_store.py: 13 passed
```

Windows acceptance command:

```powershell
py -3.13 -m pip install -e ".[dev,world,cognition]"
powershell -ExecutionPolicy Bypass -File .\tools\verify_entity_stage7_conversation_slice3a_windows.ps1
```

Expected terminal marker:

```text
PASS: Stage 7 Conversation Slice 3A Windows acceptance completed
```

## Explicitly deferred

- Assistant response generation and turn orchestration.
- Session/turn history query API and conversation-specific storage tables.
- Automatic Memory recall injection into Cognition prompts.
- CLI/UI conversation client.
- Streaming, cancellation and multi-turn generation lifecycle.

These are separate slices and must be designed against the Stage 7 Master Plan and existing Entity Runtime contracts before implementation.
