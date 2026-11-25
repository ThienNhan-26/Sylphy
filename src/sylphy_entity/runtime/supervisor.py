"""The sole lifecycle authority for one SYLPHY Entity process."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
import os
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import uvicorn

from sylphy_entity.contracts.events import EntityEvent
from sylphy_entity.contracts.observation import Observation
from sylphy_entity.contracts.decision import BrainState, CognitionFeedback
from sylphy_entity.world import WorldModel, WorldProjector
from sylphy_entity.world.adapters.filesystem import FilesystemAdapter
from sylphy_entity.interface.local_api import create_app
from .event_loop import EntityEventLoop
from .instance_lock import InstanceLock
from .lifecycle import Lifecycle, LifecycleState
from .process_manager import ProcessManager, WorkerResult, WorkerState
from sylphy_entity.persistence.database import EntityDatabase
from sylphy_entity.persistence.event_store import EventStore
from sylphy_entity.persistence.recovery import RecoveryManager, RecoveryReport
from sylphy_entity.persistence.runtime_store import RuntimeStore
from sylphy_entity.persistence.state_repository import StateRepository
from sylphy_entity.execution.dispatcher import IntentDispatcher
from sylphy_entity.execution.permissions import PermissionPolicy
from sylphy_entity.execution.resource_arbiter import ResourceArbiter
from sylphy_entity.execution.worker_executor import SandboxWorkerExecutor
from sylphy_entity.brain.core import EntityBrain, inspection_intent_id
from sylphy_entity.contracts.decision import GoalFeedback, GoalOutcome, GoalStatus
from sylphy_entity.contracts.execution import (
    ExecutionStatus, ResourceMode, ResourceRequirement,
)
from sylphy_entity.execution.intent_router import IntentRouter
from sylphy_entity.execution.resource_arbiter import ResourceBusy
from sylphy_entity.contracts.cognition import (
    CognitionOperation, CognitionOutcome, CognitionRequest, CognitionStatus,
)
from sylphy_entity.cognition.interface import CognitionInterface
from sylphy_entity.voice import VoiceCapability, VoiceSession
from sylphy_entity.embodiment import BehaviorArbiter, BehaviorAnimationMapper, to_avatar_event

logger = logging.getLogger(__name__)


class EntitySupervisor:
    """Owns daemon lifecycle, event scheduling, local API and worker supervision."""

    def __init__(
        self,
        *,
        lock_path: Path,
        port: int = 8765,
        tick_interval_seconds: float = 1.0,
        worker_stop_grace_seconds: float = 3.0,
        startup_timeout_seconds: float = 10.0,
        sandbox_root: Path | None = None,
        reconcile_interval_seconds: float = 2.0,
        autonomy_enabled: bool = False,
        cognition_provider: CognitionInterface | None = None,
        cognition_model_name: str | None = None,
        voice_capability: VoiceCapability | None = None,
        conversation_generator=None,
    ) -> None:
        if not 0 <= port <= 65535:
            raise ValueError("port must be between 0 and 65535")
        self._lifecycle = Lifecycle()
        self._lock = InstanceLock(lock_path)
        self._port = port
        self._tick_interval_seconds = tick_interval_seconds
        self._startup_timeout_seconds = startup_timeout_seconds
        self._sandbox_root = Path(sandbox_root).expanduser() if sandbox_root is not None else None
        self._reconcile_interval_seconds = reconcile_interval_seconds
        self._world_model = WorldModel()
        self._world_projector = WorldProjector(self._world_model)
        self._filesystem_adapter: FilesystemAdapter | None = None
        self._started_at: datetime | None = None
        self._runtime_dir = Path(lock_path).parent
        self._database = EntityDatabase(self._runtime_dir / "entity_runtime.sqlite3")
        self._runtime_store = RuntimeStore(database=self._database)
        self._event_store = EventStore(self._database)
        self._state_repository = StateRepository(self._database)
        self._recovery_manager = RecoveryManager(self._database, self._event_store)
        self._resource_arbiter = ResourceArbiter()
        self._recovery_report: RecoveryReport | None = None
        self._entity_id: str | None = None
        self._brain_state = BrainState()
        self._brain_state_revision = 0
        self._last_projected_event_sequence = 0
        self._boot_info = None
        self._token_path = self._runtime_dir / "control.token"
        self._control_token: str | None = None
        self._started_monotonic: float | None = None
        self._last_tick_dt: float | None = None
        self._last_event: EntityEvent | None = None
        self._brain = EntityBrain()
        self._autonomy_opt_in = bool(autonomy_enabled)
        self._autonomy_enabled = bool(autonomy_enabled)
        self._autonomy_paused = False
        self._pending_feedback: list[GoalFeedback] = []
        self._cognition_provider = cognition_provider
        self._cognition_model_name = cognition_model_name or (
            type(cognition_provider).__name__ if cognition_provider is not None else None
        )
        self._voice_capability = voice_capability
        # Avatar is a read-only presentation projection. Entity events remain
        # the sole source of truth; the renderer cannot mutate this arbiter.
        self._avatar_arbiter = BehaviorArbiter()
        self._avatar_mapper = BehaviorAnimationMapper()
        self._avatar_presentation_revision = 0
        self._avatar_presentation: dict[str, Any] = {
            "revision": 0, "behavior": "idle", "commands": []
        }
        from sylphy_entity.memory.repository import MemoryRepository
        from sylphy_entity.memory.recall import MemoryRecallService
        from sylphy_entity.interaction.conversation_runtime import ConversationRuntime
        self._conversation = ConversationRuntime(
            publisher=self, event_store=self._event_store,
            generator=conversation_generator,
            memory=MemoryRecallService(MemoryRepository(self._database)),
            voice=self._voice_capability,
        )
        self._cognition_tasks: dict[str, asyncio.Task[None]] = {}
        self._cognition_requested_ids: set[str] = set()
        self._cognition_completed_ids: set[str] = set()
        self._autonomous_intent_inflight = False
        self._autonomous_intent_history: list[dict[str, Any]] = []
        # UNKNOWN side-effect outcomes quarantine only the affected file target.
        # The first fresh post-restart observation establishes the baseline revision;
        # a later changed/deleted/renamed observation releases it durably.
        self._recovery_blocked_targets: dict[str, dict[str, Any]] = {}
        self._recovery_unknown_goal_ids: set[str] = set()
        self._notices: list[dict[str, Any]] = []
        self._dispatcher: IntentDispatcher | None = None
        self._permission_policy: PermissionPolicy | None = None
        self._intent_router = IntentRouter(timeout_seconds=15.0, deadline_seconds=60.0)
        self._event_loop = EntityEventLoop(
            on_event=self._handle_event,
            on_tick=self._handle_tick,
            tick_interval_seconds=tick_interval_seconds,
        )
        self.processes = ProcessManager(
            stop_grace_seconds=worker_stop_grace_seconds,
            on_result=self._on_worker_result,
            on_started=self._on_worker_started,
        )
        self._api_server: uvicorn.Server | None = None
        self._api_task: asyncio.Task[None] | None = None
        self._shutdown_lock = asyncio.Lock()
        self._shutdown_complete = asyncio.Event()

    def configure_voice_output(self, *, tts, output) -> None:
        if self.lifecycle not in {LifecycleState.STARTING, LifecycleState.READY, LifecycleState.RUNNING}:
            raise RuntimeError("Voice Output can only be configured before or during Entity startup")
        if self._voice_capability is not None:
            raise RuntimeError("Voice Output is already configured")
        from sylphy_entity.voice import VoiceRuntime
        self._voice_capability = VoiceCapability(VoiceRuntime(tts=tts, output=output), self)
        self._conversation.set_voice_capability(self._voice_capability)

    @property
    def lifecycle(self) -> LifecycleState:
        return self._lifecycle.state

    @property
    def api_port(self) -> int:
        if self._api_server and self._api_server.servers:
            sockets = self._api_server.servers[0].sockets
            if sockets:
                return int(sockets[0].getsockname()[1])
        return self._port

    async def start(self) -> None:
        if self.lifecycle is not LifecycleState.STARTING:
            if self.lifecycle in {LifecycleState.READY, LifecycleState.RUNNING}:
                return
            raise RuntimeError(f"Cannot start Entity in state {self.lifecycle.value}")
        try:
            self._lock.acquire()
            self._boot_info = self._runtime_store.begin_runtime()
            self._entity_id = self._state_repository.entity_id
            self._recovery_report = self._recovery_manager.recover(
                self._boot_info.previous_shutdown_clean
            )
            self._load_recovery_target_quarantine()
            self._restore_persisted_state()
            self._write_control_token()
            self._started_at = datetime.now(timezone.utc)
            self._started_monotonic = time.monotonic()
            await self._event_loop.start()
            await self._conversation.recover_interrupted()
            if self._sandbox_root is not None:
                self._filesystem_adapter = FilesystemAdapter(
                    self._sandbox_root,
                    self.publish_observation,
                    reconcile_interval_seconds=self._reconcile_interval_seconds,
                )
                await self._filesystem_adapter.start()
                self._permission_policy = self.create_permission_policy(self._sandbox_root)
                worker_executor = self.create_sandbox_worker_executor()
                self._dispatcher = self.create_intent_dispatcher(
                    self._permission_policy,
                    {"inspect_file_metadata": worker_executor},
                )
                # Observation is enabled by explicit sandbox scope. Autonomous
                # execution remains opt-in via --autonomy or the authenticated API.
            assert self._control_token is not None
            app = create_app(self, self._control_token)
            config = uvicorn.Config(
                app,
                host="127.0.0.1",
                port=self._port,
                log_config=None,
                access_log=False,
                lifespan="off",
            )
            self._api_server = uvicorn.Server(config)
            self._api_task = asyncio.create_task(
                self._api_server.serve(), name="sylphy-entity-local-api"
            )
            await self._wait_for_api_start()
            self._lifecycle.transition(LifecycleState.READY)
            self._lifecycle.transition(LifecycleState.RUNNING)
            self._resume_pending_cognition_requests()
            logger.info("SYLPHY Entity Daemon running at http://127.0.0.1:%s", self.api_port)
        except BaseException:
            if self.lifecycle is not LifecycleState.ERROR:
                try:
                    self._lifecycle.transition(LifecycleState.ERROR)
                except Exception:
                    logger.exception("Could not mark failed Entity startup")
            await self._cleanup_failed_start()
            raise

    async def _wait_for_api_start(self) -> None:
        assert self._api_server is not None
        assert self._api_task is not None
        deadline = asyncio.get_running_loop().time() + self._startup_timeout_seconds
        while not self._api_server.started:
            if self._api_task.done():
                exception = self._api_task.exception()
                if exception is not None:
                    raise RuntimeError("Entity local API failed to start") from exception
                raise RuntimeError("Entity local API stopped during startup")
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError("Timed out waiting for the Entity local API")
            await asyncio.sleep(0.02)

    async def _cleanup_failed_start(self) -> None:
        if self._api_server is not None:
            self._api_server.should_exit = True
        if self._api_task is not None:
            try:
                await asyncio.wait_for(self._api_task, timeout=2.0)
            except BaseException:
                self._api_task.cancel()
                await asyncio.gather(self._api_task, return_exceptions=True)
        if self._filesystem_adapter is not None:
            try:
                await self._filesystem_adapter.stop()
            except Exception:
                logger.exception("Could not stop filesystem adapter after startup failure")
        await self._stop_cognition_tasks()
        try:
            await self._event_loop.stop()
        except Exception:
            logger.exception("Could not stop Entity event loop after startup failure")
        try:
            await self.processes.close()
        except Exception:
            logger.exception("Could not close Entity workers after startup failure")
        finally:
            self._runtime_store.close()
            self._database.close()
            self._remove_control_token()
            self._lock.release()

    def _write_control_token(self) -> None:
        self._runtime_dir.mkdir(parents=True, exist_ok=True)
        token = secrets.token_urlsafe(32)
        temporary = self._token_path.with_suffix(".tmp")
        temporary.write_text(token, encoding="utf-8")
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            # Windows ACLs on the per-user LocalAppData directory are the
            # primary boundary; chmod is best-effort there.
            pass
        os.replace(temporary, self._token_path)
        self._control_token = token

    def _remove_control_token(self) -> None:
        token = self._control_token
        self._control_token = None
        if token is None:
            return
        try:
            # A failed second-instance startup must never delete the first
            # daemon's control token. Remove only the token this instance wrote.
            current = self._token_path.read_text(encoding="utf-8").strip()
            if secrets.compare_digest(current, token):
                self._token_path.unlink(missing_ok=True)
        except FileNotFoundError:
            pass
        except OSError:
            logger.warning("Could not remove local Entity control token", exc_info=True)

    async def request_shutdown(self) -> None:
        # Do not await shutdown from the HTTP request task: Uvicorn waits for
        # active request tasks while shutting down, so that would deadlock.
        asyncio.create_task(self.shutdown(), name="sylphy-entity-requested-shutdown")

    def voice_status(self) -> dict[str, Any]:
        if self._voice_capability is None:
            snapshot: dict[str, Any] = {
                "enabled": False,
                "state": "disabled",
                "is_speaking": False,
                "active_session_id": None,
                "session_generation": 0,
                "sequence": 0,
                "position_seconds": 0.0,
                "duration_seconds": 0.0,
                "amplitude": 0.0,
            }
        else:
            snapshot = self._voice_capability.status()
        # Session generations are scoped to one daemon process. Expose the
        # existing Entity instance identity so Avatar can safely reset its
        # stale-event guard after a genuine Entity restart.
        snapshot["entity_instance_id"] = (
            self._boot_info.instance_id if self._boot_info is not None else None
        )
        return snapshot

    async def speak_text(self, text: str, voice: str, language: str, speed: float) -> dict[str, Any]:
        if self._voice_capability is None:
            raise RuntimeError("Voice Output is disabled; start the daemon with --voice-model and --voice-voices")
        session = VoiceSession(text=text, voice=voice, language=language, speed=speed)
        # Run playback as a managed task: HTTP returns after the session is accepted,
        # while playback events flow through the existing durable Entity ingress.
        task = asyncio.create_task(self._voice_capability.speak(session),
                                   name=f"sylphy-voice-session:{session.session_id}")
        task.add_done_callback(self._log_voice_task_failure)
        return {"accepted": True, "session_id": session.session_id}

    @staticmethod
    def _log_voice_task_failure(task: asyncio.Task) -> None:
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            logger.error("Voice session failed", exc_info=(type(error), error, error.__traceback__))

    async def stop_voice(self) -> dict[str, Any]:
        if self._voice_capability is None:
            return {"stopped": False, "reason": "voice_disabled"}
        await self._voice_capability.stop("user_requested")
        return {"stopped": True}

    async def chat(self, text: str, session_id: str, channel: str = "local-api") -> dict[str, Any]:
        from sylphy_entity.contracts.conversation import ConversationMessage
        message = ConversationMessage(text=text, session_id=session_id, channel=channel)
        return await self._conversation.submit(message)

    def conversation_status(self) -> dict[str, Any]:
        return self._conversation.status()

    def conversation_history(self, session_id: str, limit: int = 40) -> list[dict[str, Any]]:
        return self._conversation.history(session_id, limit=limit)

    async def publish_event(self, event: EntityEvent) -> None:
        # External callers (including the authenticated local API) cannot forge
        # provider-owned cognition results or request-journal events.
        if event.event_type.startswith("cognition."):
            raise PermissionError("cognition events are owned by the Runtime capability")
        await self._append_and_queue_event(event)

    async def _publish_internal_cognition_event(self, event: EntityEvent) -> None:
        if not event.event_type.startswith("cognition."):
            raise ValueError("internal cognition publisher accepts cognition events only")
        await self._append_and_queue_event(event)

    async def _append_and_queue_event(self, event: EntityEvent) -> None:
        # Durability precedes queue acceptance: a process crash after this
        # commit but before projection is recovered by replay on next boot.
        append_result = self._event_store.append(event)
        if append_result.sequence <= self._last_projected_event_sequence:
            return
        await self._event_loop.publish(event)

    async def publish_observation(self, observation: Observation) -> None:
        """Accept an adapter observation as a non-executable world event."""
        await self.publish_event(
            EntityEvent(
                event_type="world.observation",
                source=observation.source_id,
                payload={"observation": observation.model_dump(mode="json")},
            )
        )

    async def _handle_event(self, event: EntityEvent) -> None:
        sequence = self._event_store.sequence_for(event.event_id)
        if sequence is None:
            raise RuntimeError(f"event {event.event_id} was not durably appended")
        if sequence <= self._last_projected_event_sequence:
            return
        self._apply_event_projection(event, sequence)
        self._project_avatar_event(event)
        if event.event_type == "cognition.result":
            self._apply_cognition_result_event(event)

    def _project_avatar_event(self, event: EntityEvent) -> None:
        """Project a durable Entity fact into a typed, presentation-only frame."""
        avatar_event = to_avatar_event(event)
        if avatar_event is None:
            return
        snapshot = self._avatar_arbiter.handle_event(avatar_event)
        frame = self._avatar_mapper.map(snapshot)
        self._avatar_presentation_revision += 1
        self._avatar_presentation = {
            "revision": self._avatar_presentation_revision,
            "behavior": frame.behavior.value,
            "commands": [
                {
                    "action": command.action.value,
                    "command_id": command.command_id,
                    "owner": command.owner,
                    "behavior": command.behavior.value,
                    "clip": command.clip,
                    "region": command.region.value,
                    "priority": command.priority,
                    "loop": command.loop,
                    "fade_in_seconds": command.fade_in_seconds,
                    "fade_out_seconds": command.fade_out_seconds,
                    "weight": command.weight,
                    "session_id": command.session_id,
                    "sequence": command.sequence,
                }
                for command in frame.commands
            ],
        }

    def avatar_presentation_status(self) -> dict[str, Any]:
        """Return a defensive copy of the latest Entity-owned Avatar frame."""
        return {
            "revision": self._avatar_presentation["revision"],
            "behavior": self._avatar_presentation["behavior"],
            "commands": [dict(command) for command in self._avatar_presentation["commands"]],
        }

    @staticmethod
    def _normalize_relative_target(value: str) -> str:
        return "/".join(part for part in value.replace("\\", "/").split("/") if part not in {"", "."})

    def _load_recovery_target_quarantine(self) -> None:
        """Quarantine exact targets of UNKNOWN autonomous metadata inspections."""
        if self._sandbox_root is None:
            return
        try:
            scope = f"sandbox:{self._sandbox_root.resolve(strict=True)}"
        except OSError:
            return
        for record in self._event_store.list_unknown_intent_targets(permission_scope=scope):
            target = self._normalize_relative_target(record["target"])
            if not target:
                continue
            entry = self._recovery_blocked_targets.setdefault(
                target, {"intent_ids": [], "baseline_revision": None}
            )
            if record["intent_id"] not in entry["intent_ids"]:
                entry["intent_ids"].append(record["intent_id"])
            goal_id = record.get("goal_id")
            if isinstance(goal_id, str) and goal_id:
                self._recovery_unknown_goal_ids.add(goal_id)

    def _release_recovery_target(self, target: str, *, reason: str, revision=None) -> None:
        normalized = self._normalize_relative_target(target)
        entry = self._recovery_blocked_targets.pop(normalized, None)
        if entry is None:
            return
        for intent_id in entry["intent_ids"]:
            self._event_store.mark_unknown_target_superseded(
                intent_id, target=normalized, reason=reason, revision=revision
            )
        logger.info(
            "Released recovered UNKNOWN target quarantine for %s (%s)", normalized, reason
        )

    def _observe_recovery_target_revision(self, observation: Observation) -> None:
        """Keep the same unresolved file revision blocked; release only on new evidence."""
        payload = dict(observation.payload)
        kind = observation.kind
        if kind == "filesystem.file_renamed":
            old_target = payload.get("old_relative_path")
            if isinstance(old_target, str):
                self._release_recovery_target(old_target, reason="target_renamed")

        relative = payload.get("relative_path")
        if not isinstance(relative, str):
            if observation.subject.startswith("file:"):
                relative = observation.subject[len("file:"):]
            else:
                return
        target = self._normalize_relative_target(relative)
        entry = self._recovery_blocked_targets.get(target)
        if entry is None:
            return

        if kind == "filesystem.file_deleted":
            self._release_recovery_target(target, reason="target_deleted")
            return
        if kind not in {
            "filesystem.file_discovered", "filesystem.file_created",
            "filesystem.file_modified", "filesystem.file_restored",
        }:
            return

        revision = (
            payload.get("size_bytes"),
            payload.get("modified_ns"),
            bool(payload.get("is_directory", False)),
        )
        baseline = entry["baseline_revision"]
        if baseline is None:
            # We cannot infer whether the file changed while the daemon was down.
            # Conservatively treat the first re-observed revision as the one whose
            # previous execution outcome is still UNKNOWN.
            entry["baseline_revision"] = revision
            return
        if revision != baseline:
            self._release_recovery_target(
                target, reason="new_file_revision_observed", revision=revision
            )

    def _world_snapshot_for_autonomy(self):
        snapshot = self._world_model.snapshot()
        if not self._recovery_blocked_targets:
            return snapshot
        blocked_subjects = {f"file:{target}" for target in self._recovery_blocked_targets}
        return snapshot.model_copy(
            update={"facts": tuple(fact for fact in snapshot.facts if fact.subject not in blocked_subjects)}
        )

    def _apply_event_projection(self, event: EntityEvent, sequence: int) -> None:
        self._last_event = event
        projected_observation_id = None
        if event.event_type == "cognition.requested":
            request_id = event.payload.get("request_id")
            if isinstance(request_id, str):
                self._cognition_requested_ids.add(request_id)
        elif event.event_type == "cognition.result":
            raw_outcome = event.payload.get("outcome")
            if isinstance(raw_outcome, dict) and isinstance(raw_outcome.get("request_id"), str):
                self._cognition_completed_ids.add(raw_outcome["request_id"])
        if event.event_type == "world.observation":
            raw = event.payload.get("observation")
            if isinstance(raw, dict):
                observation = Observation.model_validate(raw)
                needs_projection = self._event_store.append_observation(observation, sequence)
                self._observe_recovery_target_revision(observation)
                if needs_projection:
                    self._world_projector.apply(observation)
                    projected_observation_id = observation.observation_id
        # Event payloads remain in the durable EventStore. World exposes only a
        # bounded event-ID trail; conversation text is never copied into facts.
        self._world_model.record_event(event.event_id)
        self._state_repository.save_world_snapshot(
            self._world_model.snapshot(), event_sequence=sequence,
            projected_observation_id=projected_observation_id,
        )
        self._last_projected_event_sequence = sequence

    async def _handle_tick(self, dt: float) -> None:
        self._last_tick_dt = dt
        if not self._autonomy_enabled or self._autonomy_paused or self._dispatcher is None:
            return
        if self._autonomous_intent_inflight:
            return
        feedback = tuple(self._pending_feedback)
        self._pending_feedback.clear()
        world_snapshot = self._world_snapshot_for_autonomy()
        frame = self._brain.step(
            dt,
            world_snapshot,
            self._brain_state,
            recovering=bool(self._recovery_report and self._recovery_report.interrupted_intent_ids),
            feedback=feedback,
            cognition_available=self._cognition_provider is not None,
        )
        self.save_brain_state(frame.next_state)
        await self._submit_cognition_proposals(frame.cognition_proposals, world_snapshot)
        if not frame.intent_proposals:
            return
        proposal = frame.intent_proposals[0]
        try:
            intent = self._intent_router.route(proposal)
        except Exception as exc:
            self._apply_goal_feedback(
                GoalFeedback(goal_id=proposal.goal_id, outcome=GoalOutcome.FAILED, reason=f"intent rejected: {exc}")
            )
            self._record_autonomy_notice("intent_rejected", proposal, error=str(exc))
            return
        self._autonomous_intent_inflight = True
        try:
            result = await self._dispatcher.execute(intent)
            self._autonomous_intent_history.append({
                "intent_id": result.intent_id,
                "goal_id": proposal.goal_id,
                "status": result.status.value,
                "evidence_ids": list(result.evidence_ids),
                "error": result.error,
                "target": intent.target,
                "capability": intent.capability,
                "source_observation_id": intent.source_observation_id,
            })
            if result.status is ExecutionStatus.SUCCEEDED:
                evidence = result.evidence_ids[0] if result.evidence_ids else result.intent_id
                self._apply_goal_feedback(
                    GoalFeedback(goal_id=proposal.goal_id, outcome=GoalOutcome.SUCCEEDED, evidence_id=evidence)
                )
                self._record_autonomy_notice("file_inspected", proposal, result=result)
            elif result.status in {ExecutionStatus.UNKNOWN, ExecutionStatus.BLOCKED}:
                self._apply_goal_feedback(
                    GoalFeedback(goal_id=proposal.goal_id, outcome=GoalOutcome.WAITING, reason=result.error)
                )
                self._record_autonomy_notice("execution_unresolved", proposal, error=result.error)
            else:
                self._apply_goal_feedback(
                    GoalFeedback(goal_id=proposal.goal_id, outcome=GoalOutcome.FAILED, reason=result.error)
                )
                self._record_autonomy_notice("execution_failed", proposal, error=result.error)
        finally:
            self._autonomous_intent_inflight = False

    def _build_cognition_request(self, proposal, world_snapshot) -> CognitionRequest | None:
        """Build a provider request only from current, known sandbox metadata."""
        selected = [
            fact for fact in world_snapshot.facts
            if fact.last_observation_id in proposal.source_refs
            and fact.state.value == "known"
        ]
        if len(selected) != len(proposal.source_refs):
            logger.info("Skipped cognition request %s: source evidence is not current", proposal.request_id)
            return None
        facts_payload = []
        allowed_metadata = {
            "relative_path", "size_bytes", "modified_ns", "is_directory", "kind"
        }
        for fact in selected:
            raw = dict(fact.value or {})
            metadata = {key: raw[key] for key in allowed_metadata if key in raw}
            facts_payload.append({
                "subject": fact.subject,
                "metadata": metadata,
                "verification": fact.verification,
                "confidence": fact.confidence,
                "permission_scope": fact.permission_scope,
            })
        return CognitionRequest(
            request_id=proposal.request_id,
            operation=CognitionOperation(proposal.operation),
            purpose=proposal.purpose,
            context={
                "task": proposal.purpose,
                "facts": facts_payload,
                "data_policy": "metadata_only; file contents are not supplied",
            },
            source_refs=proposal.source_refs,
            max_output_tokens=1024,
            timeout_seconds=90.0,
        )

    async def _submit_cognition_proposals(self, proposals, world_snapshot) -> None:
        if self._cognition_provider is None:
            return
        for proposal in proposals:
            request_id = proposal.request_id
            if (
                request_id in self._cognition_requested_ids
                or request_id in self._cognition_completed_ids
                or request_id in self._cognition_tasks
            ):
                continue
            request = self._build_cognition_request(proposal, world_snapshot)
            if request is None:
                continue
            # The request is durable before the provider is called. Its stable
            # event ID makes a replay idempotent after a process interruption.
            request_event = EntityEvent(
                event_id=f"cognition-request-{request_id}",
                event_type="cognition.requested",
                source="entity.brain",
                payload={
                    "request_id": request_id,
                    "request": request.model_dump(mode="json"),
                },
            )
            self._event_store.append(request_event)
            self._cognition_requested_ids.add(request_id)
            task = asyncio.create_task(
                self._run_cognition_request(request),
                name=f"sylphy-cognition-{request_id}",
            )
            self._cognition_tasks[request_id] = task

    async def _run_cognition_request(self, request: CognitionRequest) -> None:
        assert self._cognition_provider is not None
        owner_id = f"cognition:{request.request_id}"
        leases = ()
        try:
            leases = await self._resource_arbiter.acquire(
                owner_id,
                (ResourceRequirement(
                    resource_key=f"llm:local:{self._cognition_model_name or 'default'}",
                    mode=ResourceMode.EXCLUSIVE,
                ),),
                ttl_seconds=request.timeout_seconds + 10.0,
            )
            outcome = await self._cognition_provider.execute(request)
        except ResourceBusy:
            outcome = CognitionOutcome(
                request_id=request.request_id, status=CognitionStatus.FAILED,
                elapsed_ms=0, error_code="cognition_resource_busy",
                error_message="Local model inference resource is currently owned",
                model=self._cognition_model_name,
            )
        except asyncio.CancelledError:
            # A request without a result remains journaled and can be retried
            # safely after restart because cognition has no external side effects.
            raise
        except Exception as exc:
            logger.exception("Cognition task failed for %s", request.request_id)
            outcome = CognitionOutcome(
                request_id=request.request_id, status=CognitionStatus.FAILED,
                elapsed_ms=0, error_code="runtime_cognition_failure",
                error_message=f"{type(exc).__name__}: {exc}"[:500],
                model=self._cognition_model_name,
            )
        finally:
            if leases:
                await self._resource_arbiter.release(leases)

        if not isinstance(outcome, CognitionOutcome):
            outcome = CognitionOutcome(
                request_id=request.request_id, status=CognitionStatus.INVALID_OUTPUT,
                elapsed_ms=0, error_code="invalid_provider_contract",
                error_message="Cognition provider returned a non-CognitionOutcome value",
                model=self._cognition_model_name,
            )
        if outcome.request_id != request.request_id:
            outcome = CognitionOutcome(
                request_id=request.request_id, status=CognitionStatus.INVALID_OUTPUT,
                elapsed_ms=max(0, outcome.elapsed_ms),
                error_code="request_id_mismatch",
                error_message="Cognition provider returned a different request ID",
                model=self._cognition_model_name,
            )
        result_event = EntityEvent(
            event_id=f"cognition-result-{request.request_id}",
            event_type="cognition.result",
            source="cognition.provider",
            payload={
                "outcome": outcome.model_dump(mode="json"),
                "source_refs": list(request.source_refs),
            },
        )
        try:
            await self._publish_internal_cognition_event(result_event)
        except RuntimeError:
            # Event loop is shutting down: the request remains unmatched in the
            # journal and will be reconciled on the next daemon startup.
            logger.info("Cognition result deferred during shutdown: %s", request.request_id)
        except Exception:
            logger.exception("Could not publish cognition result event for %s", request.request_id)
        finally:
            self._cognition_tasks.pop(request.request_id, None)

    def _cognition_feedback_from_event(self, event: EntityEvent) -> CognitionFeedback:
        raw = event.payload.get("outcome")
        if not isinstance(raw, dict):
            raise ValueError("cognition.result event has no outcome object")
        outcome = CognitionOutcome.model_validate(raw)
        source_refs = tuple(str(item) for item in event.payload.get("source_refs", ()))
        result = outcome.result
        return CognitionFeedback(
            request_id=outcome.request_id,
            status=outcome.status.value,
            source_refs=source_refs,
            summary=result.summary if result else "",
            summary_evidence_refs=result.summary_evidence_refs if result else (),
            findings=tuple(
                (item.kind, item.statement, item.confidence, item.evidence_refs)
                for item in (result.findings if result else ())
            ),
            proposals=tuple(
                (item.kind, item.subject, item.rationale, item.evidence_refs)
                for item in (result.proposals if result else ())
            ),
            elapsed_ms=outcome.elapsed_ms,
            model=outcome.model,
            error_code=outcome.error_code,
        )

    def _apply_cognition_result_event(self, event: EntityEvent) -> None:
        feedback = self._cognition_feedback_from_event(event)
        if any(item.request_id == feedback.request_id for item in self._brain_state.cognition_receipts):
            return
        frame = self._brain.step(
            0.0,
            self._world_snapshot_for_autonomy(),
            self._brain_state,
            cognition_feedback=(feedback,),
            allow_cognition_requests=False,
            allow_goal_proposals=False,
        )
        self.save_brain_state(frame.next_state)

    def _iter_journal_events(self):
        sequence = 0
        while True:
            batch = self._event_store.read_after(sequence, limit=500)
            if not batch:
                return
            for stored in batch:
                yield stored.event
                sequence = stored.sequence
            if len(batch) < 500:
                return

    def _replay_unprocessed_cognition_results(self) -> None:
        for event in self._iter_journal_events():
            if event.event_type != "cognition.result":
                continue
            try:
                self._apply_cognition_result_event(event)
            except Exception:
                logger.exception("Could not replay cognition result event %s", event.event_id)

    def _resume_pending_cognition_requests(self) -> None:
        if self._cognition_provider is None:
            return
        requests: dict[str, CognitionRequest] = {}
        completed: set[str] = set()
        for event in self._iter_journal_events():
            if event.event_type == "cognition.requested":
                raw = event.payload.get("request")
                if isinstance(raw, dict):
                    request = CognitionRequest.model_validate(raw)
                    requests[request.request_id] = request
            elif event.event_type == "cognition.result":
                raw = event.payload.get("outcome")
                if isinstance(raw, dict) and isinstance(raw.get("request_id"), str):
                    completed.add(raw["request_id"])
        self._cognition_requested_ids.update(requests)
        self._cognition_completed_ids.update(completed)
        current_refs = {
            fact.last_observation_id for fact in self._world_model.snapshot().facts
            if fact.state.value == "known"
        }
        for request_id, request in requests.items():
            if request_id in completed or request_id in self._cognition_tasks:
                continue
            if not set(request.source_refs).issubset(current_refs):
                # The old source is not fresh after restart; do not reason from it.
                stale_outcome = CognitionOutcome(
                    request_id=request_id, status=CognitionStatus.FAILED, elapsed_ms=0,
                    error_code="stale_source_after_restart",
                    error_message="Source observation must be refreshed before cognition",
                    model=self._cognition_model_name,
                )
                stale_event = EntityEvent(
                    event_id=f"cognition-result-{request_id}", event_type="cognition.result",
                    source="cognition.recovery",
                    payload={"outcome": stale_outcome.model_dump(mode="json"),
                             "source_refs": list(request.source_refs)},
                )
                self._event_store.append(stale_event)
                self._cognition_completed_ids.add(request_id)
                self._apply_cognition_result_event(stale_event)
                continue
            task = asyncio.create_task(
                self._run_cognition_request(request), name=f"sylphy-cognition-recovered-{request_id}"
            )
            self._cognition_tasks[request_id] = task

    def _apply_goal_feedback(self, feedback: GoalFeedback) -> None:
        """Commit evidence-backed outcome into Brain state immediately.

        This closes the crash window between a durable execution result and the
        next periodic tick. A restart can also replay the durable success below.
        """
        # Apply the result without admitting a new goal in this same helper.
        # The next scheduled tick evaluates the real World and may select the
        # next candidate; otherwise it could create an ACTIVE goal whose
        # proposal this feedback-only call would discard.
        feedback_world = self._world_model.snapshot().model_copy(update={"facts": ()})
        frame = self._brain.step(
            0.0, feedback_world, self._brain_state, feedback=(feedback,)
        )
        self.save_brain_state(frame.next_state)

    def _record_autonomy_notice(self, kind: str, proposal, *, result=None, error=None) -> None:
        notice = {
            "at": datetime.now(timezone.utc).isoformat(),
            "kind": kind,
            "goal_id": proposal.goal_id,
            "intent_id": getattr(proposal, "intent_id", None),
            "source_observation_id": (
                getattr(proposal, "source_observation_id", None)
                or getattr(proposal, "source", None)
            ),
            "target": proposal.target,
            "reason": proposal.reason,
            "status": result.status.value if result is not None else None,
            "evidence_ids": list(result.evidence_ids) if result is not None else [],
            "error": error,
        }
        self._notices.append(notice)
        del self._notices[:-100]
        logger.info("Entity autonomy notice: %s", notice)

    async def pause_autonomy(self) -> dict[str, Any]:
        self._autonomy_paused = True
        self._autonomy_enabled = False
        cancelled = ()
        if self._dispatcher is not None:
            cancelled = await self._dispatcher.pause_autonomy()
        return {"paused": True, "cancelled_intents": list(cancelled)}

    async def resume_autonomy(self) -> dict[str, Any]:
        if self._dispatcher is not None:
            await self._dispatcher.resume_autonomy()
        self._autonomy_paused = False
        # Resume clears the user pause but cannot grant opt-in by itself.
        self._autonomy_enabled = self._autonomy_opt_in and self._sandbox_root is not None
        return {
            "paused": False,
            "enabled": self._autonomy_enabled,
            "opt_in_required": not self._autonomy_enabled,
        }

    async def revoke_permission(self, scope: str | None = None) -> dict[str, Any]:
        if self._permission_policy is None or self._dispatcher is None:
            raise RuntimeError("no sandbox permission scope is configured")
        expected = self._permission_policy.scope
        selected = scope or expected
        if selected != expected:
            raise PermissionError("only the configured sandbox permission scope can be revoked")
        cancelled = await self._dispatcher.revoke_scope(selected)
        return {"revoked": True, "scope": selected, "cancelled_intents": list(cancelled)}

    async def restore_permission(self, scope: str | None = None) -> dict[str, Any]:
        if self._permission_policy is None or self._dispatcher is None:
            raise RuntimeError("no sandbox permission scope is configured")
        expected = self._permission_policy.scope
        selected = scope or expected
        if selected != expected:
            raise PermissionError("only the configured sandbox permission scope can be restored")
        await self._dispatcher.restore_scope(selected)
        return {"restored": True, "scope": selected}

    async def start_worker(self, name: str, argv: Sequence[str]) -> None:
        await self.processes.start(name, argv)

    def _on_worker_started(self, worker) -> None:
        if self._boot_info is None or not self._database.is_open:
            raise RuntimeError("Entity worker cannot start before durable Runtime startup")
        intent_id = (
            worker.name[len("intent-"):] if worker.name.startswith("intent-")
            else f"worker:{worker.name}"
        )
        self._event_store.record_worker_started(
            intent_id=intent_id, pid=worker.pid, create_time=worker.create_time,
            daemon_instance_id=self._boot_info.instance_id, worker_name=worker.name,
        )

    async def _on_worker_result(self, result: WorkerResult) -> None:
        if self._database.is_open:
            worker_state = {
                WorkerState.EXITED: "EXITED",
                WorkerState.FAILED: "FAILED",
                WorkerState.CANCELLED: "CANCELLED",
            }.get(result.state)
            if worker_state is not None:
                self._event_store.record_worker_finished(
                    pid=result.pid, create_time=result.create_time,
                    return_code=result.return_code, state=worker_state,
                )
        if result.state is WorkerState.FAILED:
            if self.lifecycle in {LifecycleState.READY, LifecycleState.RUNNING}:
                self._lifecycle.transition(LifecycleState.DEGRADED)

    def world_snapshot(self) -> dict[str, Any]:
        return self._world_model.snapshot().model_dump(mode="json")

    def brain_state_snapshot(self) -> dict[str, Any]:
        from sylphy_entity.persistence.state_repository import brain_state_to_dict
        return brain_state_to_dict(self._brain_state)

    def save_brain_state(self, state: BrainState) -> int:
        """Persist Brain output at the Runtime boundary; Brain itself stays pure."""
        self._brain_state = state
        self._brain_state_revision = self._state_repository.save_brain_state(
            state, metadata={"runtime_instance_id": self._boot_info.instance_id if self._boot_info else None}
        )
        return self._brain_state_revision

    def create_permission_policy(self, sandbox_root: Path, *, max_read_bytes: int = 64 * 1024) -> PermissionPolicy:
        """Create a sandbox policy whose revocations survive daemon restarts."""
        if not self._database.is_open:
            raise RuntimeError("Entity Daemon must be started before creating persistent policies")
        return PermissionPolicy(
            sandbox_root, max_read_bytes=max_read_bytes, state_store=self._state_repository
        )

    def create_sandbox_worker_executor(self, *, read_limit_bytes: int = 64 * 1024) -> SandboxWorkerExecutor:
        """Create the read-only worker with durable PID + creation-time tracking."""
        if not self._database.is_open or self._boot_info is None:
            raise RuntimeError("Entity Daemon must be started before creating persistent workers")
        return SandboxWorkerExecutor(self.processes, read_limit_bytes=read_limit_bytes)

    def create_intent_dispatcher(self, permission_policy: PermissionPolicy, handlers) -> IntentDispatcher:
        """Bind Stage-4 dispatch to the Stage-5 durable execution journal."""
        if not self._database.is_open:
            raise RuntimeError("Entity Daemon must be started before creating a persistent dispatcher")
        return IntentDispatcher(
            permission_policy=permission_policy, resource_arbiter=self._resource_arbiter,
            handlers=handlers, journal=self._event_store,
        )

    def _restore_persisted_state(self) -> None:
        snapshot, checkpoint = self._state_repository.load_latest_world_snapshot()
        if snapshot is not None:
            self._world_model.restore(snapshot)
        self._last_projected_event_sequence = checkpoint
        # Replay durable events committed after the last completed projection.
        while True:
            pending = self._event_store.read_after(self._last_projected_event_sequence, limit=500)
            if not pending:
                break
            for stored in pending:
                self._apply_event_projection(stored.event, stored.sequence)
            if len(pending) < 500:
                break
        # A persisted fact describes the last known world, not the world now.
        # It must be re-observed before Brain can use it as current evidence.
        self._world_model.mark_all_stale()
        self._state_repository.save_world_snapshot(
            self._world_model.snapshot(), event_sequence=self._event_store.latest_sequence()
        )
        self._last_projected_event_sequence = self._event_store.latest_sequence()
        restored = self._state_repository.load_brain_state(restore_transients=True)
        self._brain_state = restored or BrainState()
        self._brain_state_revision = self._state_repository.latest_revision()
        if self._recovery_unknown_goal_ids:
            recovered_goals = tuple(
                replace(
                    goal, status=GoalStatus.UNKNOWN,
                    updated_at_virtual=self._brain_state.virtual_time,
                    suspended_at_virtual=None, resume_status=None,
                )
                if goal.goal_id in self._recovery_unknown_goal_ids
                and goal.status in {GoalStatus.ACTIVE, GoalStatus.WAITING, GoalStatus.SUSPENDED}
                else goal
                for goal in self._brain_state.goals
            )
            if recovered_goals != self._brain_state.goals:
                self._brain_state = replace(self._brain_state, goals=recovered_goals)
                self._brain_state_revision = self._state_repository.save_brain_state(
                    self._brain_state,
                    metadata={"recovered_unknown_goal_ids": sorted(self._recovery_unknown_goal_ids)},
                )
        # If the daemon stopped after a verified execution commit but before
        # the next Brain tick, replay that exact attempt's feedback. Never infer
        # success from an unresolved/UNKNOWN Intent.
        goals_by_id = {goal.goal_id: goal for goal in self._brain_state.goals}
        successful = self._event_store.list_successful_intent_results()
        for record in successful:
            goal = goals_by_id.get(record["goal_id"])
            if goal is None or goal.status not in {GoalStatus.ACTIVE, GoalStatus.WAITING}:
                continue
            expected_id = inspection_intent_id(goal.goal_id, goal.attempt_count)
            if record["intent_id"] != expected_id or not record["evidence_ids"]:
                continue
            evidence_id = record["evidence_ids"][0]
            if evidence_id in goal.completion_evidence:
                continue
            self._apply_goal_feedback(
                GoalFeedback(goal_id=goal.goal_id, outcome=GoalOutcome.SUCCEEDED, evidence_id=evidence_id)
            )
            goals_by_id[goal.goal_id] = next(
                (item for item in self._brain_state.goals if item.goal_id == goal.goal_id), goal
            )
        if restored is None:
            self._brain_state_revision = self._state_repository.save_brain_state(
                self._brain_state, metadata={"created_on_first_boot": True}
            )
        # Seed cognition idempotency from the complete durable journal before the
        # event loop can tick. This covers a crash between request append and
        # Brain-state checkpoint.
        for journal_event in self._iter_journal_events():
            if journal_event.event_type == "cognition.requested":
                request_id = journal_event.payload.get("request_id")
                if isinstance(request_id, str):
                    self._cognition_requested_ids.add(request_id)
            elif journal_event.event_type == "cognition.result":
                raw_outcome = journal_event.payload.get("outcome")
                if isinstance(raw_outcome, dict) and isinstance(raw_outcome.get("request_id"), str):
                    self._cognition_completed_ids.add(raw_outcome["request_id"])
        # Results are durable events. Re-apply any result whose Brain receipt was
        # not committed before a crash; stale pre-restart observations are rejected.
        self._replay_unprocessed_cognition_results()

    def memory_status(self, limit: int = 50) -> dict[str, Any]:
        """Read-only memory inspection; content is omitted from the default status view."""
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        from sylphy_entity.memory.repository import MemoryRepository, MemoryCaptureService

        repository = MemoryRepository(self._database)
        capture = MemoryCaptureService(self._database)
        active = repository.list_active(limit=limit)
        pending = capture.list_pending(limit=limit)
        return {
            "active_count_returned": len(active),
            "pending_count_returned": len(pending),
            "limit": limit,
            "active": [
                {
                    "memory_id": item.memory_id,
                    "category": item.category,
                    "origin": item.origin.value,
                    "confidence": item.confidence,
                    "source_refs": list(item.source_refs),
                    "updated_at": item.updated_at.isoformat(),
                }
                for item in active
            ],
            "pending": [
                {
                    "candidate_id": item.candidate_id,
                    "category": item.category,
                    "kind": item.kind.value,
                    "status": item.status.value,
                    "confidence": item.confidence,
                    "source_refs": list(item.source_refs),
                    "updated_at": item.updated_at.isoformat(),
                }
                for item in pending
            ],
        }

    def status(self) -> dict[str, Any]:
        metrics = self._event_loop.metrics
        return {
            "entity": "SYLPHY",
            "runtime": "entity-daemon",
            "lifecycle": self.lifecycle.value,
            "started_at": self._started_at.isoformat() if self._started_at else None,
            "persistence": {
                "entity_id": self._entity_id,
                "state_revision": self._brain_state_revision,
                "last_projected_event_sequence": self._last_projected_event_sequence,
                "recovery": self._recovery_report.to_dict() if self._recovery_report else None,
                "boot_count": self._boot_info.boot_count if self._boot_info else None,
                "instance_id": self._boot_info.instance_id if self._boot_info else None,
                "previous_shutdown_clean": (
                    self._boot_info.previous_shutdown_clean if self._boot_info else None
                ),
                "previous_shutdown_at": (
                    self._boot_info.previous_shutdown_at if self._boot_info else None
                ),
            },
            "uptime_seconds": (
                max(0.0, time.monotonic() - self._started_monotonic)
                if self._started_monotonic is not None
                else 0.0
            ),
            "api": {"host": "127.0.0.1", "port": self.api_port},
            "scheduler": {
                "running": self._event_loop.running,
                "tick_interval_seconds": self._tick_interval_seconds,
                "ticks": metrics.ticks,
                "last_tick_dt": self._last_tick_dt,
            },
            "events": {
                "received": metrics.events_received,
                "processed": metrics.events_processed,
                "last_event_id": metrics.last_event_id,
                "last_event_type": self._last_event.event_type if self._last_event else None,
            },
            "voice": self.voice_status(),
            "conversation": self.conversation_status(),
            "cognition": {
                "enabled": self._cognition_provider is not None,
                "provider": self._cognition_model_name,
                "inflight": sorted(self._cognition_tasks),
                "requested": len(self._cognition_requested_ids),
                "completed": len(self._cognition_completed_ids),
                "reviewed": len(self._brain_state.cognition_receipts),
                # Expose only review metadata, never model text or sandbox data.
                # This makes live acceptance inspectable through /status.
                "recent_reviews": [
                    {
                        "request_id": receipt.request_id,
                        "status": receipt.status,
                        "accepted_as_evidence": receipt.accepted_as_evidence,
                        "review_reason": receipt.review_reason,
                        "model": receipt.model,
                        "elapsed_ms": receipt.elapsed_ms,
                        "error_code": receipt.error_code,
                    }
                    for receipt in self._brain_state.cognition_receipts[-8:]
                ],
            },
            "autonomy": {
                "enabled": self._autonomy_enabled,
                "opt_in": self._autonomy_opt_in,
                "paused": self._autonomy_paused,
                "loop": "world-observation -> brain -> goal -> intent -> read-only execution -> feedback",
                "inflight": self._autonomous_intent_inflight,
                "history": self._autonomous_intent_history[-20:],
                "notices": self._notices[-20:],
                "pending_feedback": len(self._pending_feedback),
                "recovery_blocked_targets": sorted(self._recovery_blocked_targets),
            },
            "brain": {
                "behaviour": self._brain_state.behaviour.value,
                "tick_count": self._brain_state.tick_count,
                "active_goals": [g.goal_id for g in self._brain_state.goals if g.status.value in {"active", "waiting"}],
                "unknown_goals": [g.goal_id for g in self._brain_state.goals if g.status is GoalStatus.UNKNOWN],
                "last_reason": self._brain_state.last_reason,
            },
            "world": {
                "version": self._world_model.version,
                "facts": self._world_model.fact_count,
                "adapter": self._filesystem_adapter.status() if self._filesystem_adapter else {
                    "id": "filesystem:sandbox",
                    "kind": "filesystem",
                    "running": False,
                    "configured": False,
                },
            },
            "workers": [
                {
                    "name": worker.name,
                    "pid": worker.pid,
                    "create_time": worker.create_time,
                    "state": worker.state.value,
                    "return_code": worker.return_code,
                }
                for worker in self.processes.snapshots()
            ],
        }

    async def _stop_cognition_tasks(self) -> None:
        tasks = tuple(self._cognition_tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._cognition_tasks.clear()
        provider = self._cognition_provider
        if provider is not None:
            await provider.close()

    async def serve_forever(self) -> None:
        if self._api_task is None:
            raise RuntimeError("Entity Daemon has not been started")
        await self._api_task

    async def shutdown(self) -> None:
        async with self._shutdown_lock:
            if self._shutdown_complete.is_set():
                return
            if self.lifecycle is LifecycleState.STOPPED:
                self._shutdown_complete.set()
                return
            if self.lifecycle is not LifecycleState.STOPPING:
                self._lifecycle.transition(LifecycleState.STOPPING)

            shutdown_clean = True
            try:
                # Stop ingress first, then scheduling and managed workers.
                if self._api_server is not None:
                    self._api_server.should_exit = True
                if self._api_task is not None and not self._api_task.done():
                    try:
                        await asyncio.wait_for(self._api_task, timeout=5.0)
                    except Exception:
                        shutdown_clean = False
                        logger.exception("Entity API did not stop cleanly")
                        self._api_task.cancel()
                        await asyncio.gather(self._api_task, return_exceptions=True)

                if self._filesystem_adapter is not None:
                    try:
                        await self._filesystem_adapter.stop()
                    except Exception:
                        shutdown_clean = False
                        logger.exception("Filesystem adapter did not stop cleanly")

                try:
                    await self._conversation.stop()
                except Exception:
                    shutdown_clean = False
                    logger.exception("Conversation Runtime did not stop cleanly")

                if self._voice_capability is not None:
                    try:
                        await self._voice_capability.stop("entity_shutdown")
                    except Exception:
                        shutdown_clean = False
                        logger.exception("Voice Output did not stop cleanly")

                try:
                    await self._stop_cognition_tasks()
                except Exception:
                    shutdown_clean = False
                    logger.exception("Cognition capability did not stop cleanly")

                try:
                    await self._event_loop.stop()
                except Exception:
                    shutdown_clean = False
                    logger.exception("Entity event loop did not stop cleanly")

                try:
                    await self.processes.close()
                except Exception:
                    shutdown_clean = False
                    logger.exception("Entity workers did not stop cleanly")

                if shutdown_clean:
                    try:
                        self._brain_state_revision = self._state_repository.save_brain_state(
                            self._brain_state, metadata={"shutdown_instance_id": self._boot_info.instance_id if self._boot_info else None}
                        )
                        self._state_repository.save_world_snapshot(
                            self._world_model.snapshot(),
                            # Do not checkpoint unprojected journal events. A
                            # later boot must replay them even after a clean stop.
                            event_sequence=self._last_projected_event_sequence,
                        )
                        self._runtime_store.mark_clean_shutdown()
                    except Exception:
                        shutdown_clean = False
                        logger.exception("Could not persist clean Entity shutdown marker")
            except asyncio.CancelledError:
                shutdown_clean = False
                raise
            finally:
                self._runtime_store.close()
                self._database.close()
                self._remove_control_token()
                self._lock.release()
                if self.lifecycle is LifecycleState.STOPPING:
                    self._lifecycle.transition(
                        LifecycleState.STOPPED if shutdown_clean else LifecycleState.ERROR
                    )
                self._shutdown_complete.set()
