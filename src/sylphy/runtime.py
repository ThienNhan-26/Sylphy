"""Operational lifecycle, events, status, heartbeat, and inference-slot control."""

from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
import logging
from threading import Condition, Event, Lock, Thread, current_thread
from typing import TYPE_CHECKING, Callable, Iterator

from .core import ConversationCancelled, ConversationService, Turn
from .cognition import CognitionTask
from .exploration import ExplorationQuestion, ExplorationTask, ExplorationService
from .investigation import Investigation, InvestigationBudget, InvestigationResult, InvestigationStatus
from .investigation_execution import InvestigationExecutor, InvestigationPreempted
from .presence import PresenceEvent, PresenceEventType, PresenceService
from .avatar import AvatarController, PresentationState, normalize_presentation_state
from .interaction_policy import (
    InteractionAction, InteractionEvent, InteractionPolicy, PolicyConditions,
)
from .vision import MAX_QUESTION_CHARS
from .companion_contract import Action, AutonomyDecision, CompanionContext, CompanionEvent, CompanionPolicy, InitiativeDecision, OutcomeStatus
from .companion_slice import (
    build_initiative_context,
    build_presence_context,
    initiative_trigger_event,
    presence_notice_event,
    propose_autonomy_decision,
    propose_initiative_decision,
    propose_presence_decision,
    record_autonomy_execution,
    record_completed_turn,
    record_initiative,
    record_presence_event,
)

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from .voice import VoiceService


class RuntimePhase(str, Enum):
    STARTING = "STARTING"
    READY = "READY"
    RUNNING = "RUNNING"
    IDLE = "IDLE"
    SHUTTING_DOWN = "SHUTTING_DOWN"
    STOPPED = "STOPPED"
    ERROR = "ERROR"


class EventType(str, Enum):
    RUNTIME_STARTED = "RUNTIME_STARTED"
    RUNTIME_READY = "RUNTIME_READY"
    RUNTIME_STATE_CHANGED = "RUNTIME_STATE_CHANGED"
    RUNTIME_SHUTTING_DOWN = "RUNTIME_SHUTTING_DOWN"
    RUNTIME_STOPPED = "RUNTIME_STOPPED"
    SESSION_CHANGED = "SESSION_CHANGED"
    USER_MESSAGE_RECEIVED = "USER_MESSAGE_RECEIVED"
    RESPONSE_STARTED = "RESPONSE_STARTED"
    RESPONSE_COMPLETED = "RESPONSE_COMPLETED"
    RESPONSE_CANCELLED = "RESPONSE_CANCELLED"
    MODEL_ERROR = "MODEL_ERROR"
    MEMORY_OPERATION = "MEMORY_OPERATION"
    HEARTBEAT = "HEARTBEAT"
    RESOURCE_LIMIT_REACHED = "RESOURCE_LIMIT_REACHED"
    COGNITION_TASK_QUEUED = "COGNITION_TASK_QUEUED"
    COGNITION_TASK_STARTED = "COGNITION_TASK_STARTED"
    COGNITION_TASK_COMPLETED = "COGNITION_TASK_COMPLETED"
    COGNITION_TASK_FAILED = "COGNITION_TASK_FAILED"


@dataclass(frozen=True)
class RuntimeEvent:
    name: EventType
    timestamp: datetime
    payload: dict[str, object]


class EventBus:
    def __init__(self):
        self._lock = Lock()
        self._handlers: dict[EventType, list[Callable[[RuntimeEvent], None]]] = {}

    def subscribe(self, event_type: EventType, handler: Callable[[RuntimeEvent], None]) -> Callable[[], None]:
        with self._lock:
            self._handlers.setdefault(event_type, []).append(handler)

        def unsubscribe() -> None:
            with self._lock:
                handlers = self._handlers.get(event_type, [])
                if handler in handlers:
                    handlers.remove(handler)
        return unsubscribe

    def publish(self, event_type: EventType, payload: dict[str, object] | None = None) -> None:
        event = RuntimeEvent(event_type, datetime.now(timezone.utc), dict(payload or {}))
        with self._lock:
            handlers = tuple(self._handlers.get(event_type, ()))
        for handler in handlers:
            try:
                handler(event)
            except Exception:
                logger.exception("Event handler failed for %s", event_type.value)

    def clear(self) -> None:
        with self._lock:
            self._handlers.clear()


@dataclass(frozen=True)
class RuntimeSnapshot:
    phase: RuntimePhase
    session_id: str | None
    model: str
    generation_active: bool
    active_operation: str | None
    last_activity: datetime
    started_at: datetime
    uptime_seconds: float
    last_error: str | None
    active_background_tasks: int


class RuntimeState:
    def __init__(self, model: str):
        now = datetime.now(timezone.utc)
        self._lock = Lock()
        self._phase = RuntimePhase.STOPPED
        self._session_id: str | None = None
        self._model = model
        self._generation_active = False
        self._active_operation: str | None = None
        self._last_activity = now
        self._started_at = now
        self._last_error: str | None = None
        self._background_tasks = 0

    def _activity(self) -> None:
        self._last_activity = datetime.now(timezone.utc)

    def set_phase(self, phase: RuntimePhase) -> None:
        with self._lock:
            self._phase = phase
            if phase == RuntimePhase.STARTING:
                self._started_at = datetime.now(timezone.utc)
            self._activity()

    def set_session(self, session_id: str | None) -> None:
        with self._lock:
            self._session_id = session_id
            self._activity()

    def begin_operation(self, name: str) -> None:
        with self._lock:
            self._active_operation = name
            self._generation_active = name == "generation"
            self._activity()

    def end_operation(self) -> None:
        with self._lock:
            self._active_operation = None
            self._generation_active = False
            self._activity()

    def touch(self) -> None:
        with self._lock:
            self._activity()

    def record_error(self, error: BaseException) -> None:
        with self._lock:
            self._last_error = f"{type(error).__name__}: {error}"
            self._activity()

    def set_background_tasks(self, count: int) -> None:
        with self._lock:
            self._background_tasks = count

    def snapshot(self) -> RuntimeSnapshot:
        with self._lock:
            now = datetime.now(timezone.utc)
            return RuntimeSnapshot(self._phase, self._session_id, self._model, self._generation_active,
                                   self._active_operation, self._last_activity, self._started_at,
                                   max(0.0, (now - self._started_at).total_seconds()), self._last_error,
                                   self._background_tasks)


class ResourceGovernor:
    _PRIORITY = {"conversation": 0, "vision": 1, "cognition": 2, "exploration": 3, "investigation": 4}

    def __init__(self, max_concurrent_generations: int = 1, on_wait: Callable[[], None] | None = None,
                 on_release: Callable[[], None] | None = None):
        if max_concurrent_generations < 1:
            raise ValueError("max_concurrent_generations must be at least 1")
        if max_concurrent_generations != 1:
            raise ValueError("ResourceGovernor currently supports one inference at a time")
        self._condition = Condition(Lock())
        self._active = False
        self._active_priority: str | None = None
        self._active_priority_cancel: Event | None = None
        self._waiters = {priority: 0 for priority in self._PRIORITY}
        self._on_wait = on_wait
        self._on_release = on_release

    @contextmanager
    def priority_reservation(self, priority: str = "conversation", cancel: Event | None = None):
        """Reserve a place in the priority order across sequential model requests."""
        if priority not in self._PRIORITY:
            raise ValueError("unsupported priority reservation")
        reservation = {"priority": priority, "queued": True}
        with self._condition:
            self._waiters[priority] += 1
            if (self._active_priority is not None
                    and self._PRIORITY[priority] < self._PRIORITY[self._active_priority]
                    and self._active_priority_cancel is not None):
                self._active_priority_cancel.set()
            self._condition.notify_all()
        try:
            if cancel and cancel.is_set():
                raise InterruptedError("Cancelled while reserving inference priority")
            yield reservation
        finally:
            with self._condition:
                if reservation["queued"]:
                    self._waiters[priority] -= 1
                    reservation["queued"] = False
                self._condition.notify_all()

    @contextmanager
    def generation(self, cancel: Event | None = None, priority: str = "conversation", *,
                   reservation=None, retain_reservation: bool = False) -> Iterator[None]:
        if priority not in self._PRIORITY:
            raise ValueError("priority must be conversation, vision, cognition, exploration, or investigation")
        acquired = False
        announced_wait = False
        priority_cancel = Event() if priority != "conversation" else None
        with self._condition:
            if reservation is not None:
                if reservation["priority"] != "conversation" or not reservation["queued"]:
                    raise ValueError("generation received an inactive foreground reservation")
                self._waiters[reservation["priority"]] -= 1
                reservation["queued"] = False
            self._waiters[priority] += 1
            if (self._active_priority is not None
                    and self._PRIORITY[priority] < self._PRIORITY[self._active_priority]
                    and self._active_priority_cancel is not None):
                self._active_priority_cancel.set()
        try:
            with self._condition:
                while self._active or any(
                    count for other, count in self._waiters.items()
                    if self._PRIORITY[other] < self._PRIORITY[priority]
                ):
                    if cancel and cancel.is_set():
                        raise InterruptedError("Cancelled while waiting for generation slot")
                    if not announced_wait:
                        announced_wait = True
                        if self._on_wait:
                            self._on_wait()
                    self._condition.wait(0.1)
                if cancel and cancel.is_set():
                    raise InterruptedError("Cancelled while waiting for generation slot")
                self._active = True
                self._active_priority = priority
                self._active_priority_cancel = priority_cancel
                acquired = True
                self._waiters[priority] -= 1
            if priority_cancel is not None and cancel is not None:
                yield _AnyCancellation(cancel, priority_cancel)
            elif priority_cancel is not None:
                yield priority_cancel
            else:
                yield cancel
        finally:
            with self._condition:
                if not acquired:
                    self._waiters[priority] -= 1
                if acquired:
                    self._active = False
                    self._active_priority = None
                    self._active_priority_cancel = None
                if reservation is not None and retain_reservation and not reservation["queued"]:
                    self._waiters[reservation["priority"]] += 1
                    reservation["queued"] = True
                self._condition.notify_all()
            if acquired and self._on_release:
                try:
                    self._on_release()
                except Exception:
                    logger.exception("Resource release callback failed")


class _AnyCancellation:
    """Read-only event view combining worker shutdown and conversation preemption."""
    def __init__(self, *events: Event):
        self._events = events

    def is_set(self) -> bool:
        return any(event.is_set() for event in self._events)


class Heartbeat:
    def __init__(self, interval_seconds: float, events: EventBus, state: RuntimeState):
        if interval_seconds <= 0:
            raise ValueError("heartbeat interval must be positive")
        self._interval = interval_seconds
        self._events = events
        self._state = state
        self._stop = Event()
        self._thread: Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = Thread(target=self._run, name="sylphy-heartbeat", daemon=True)
        self._state.set_background_tasks(1)
        self._thread.start()

    def _run(self) -> None:
        try:
            while not self._stop.wait(self._interval):
                try:
                    snapshot = self._state.snapshot()
                    self._events.publish(EventType.HEARTBEAT, {
                        "phase": snapshot.phase.value,
                        "generation_active": snapshot.generation_active,
                        "uptime_seconds": snapshot.uptime_seconds,
                    })
                except Exception:
                    logger.exception("Heartbeat iteration failed")
        finally:
            self._state.set_background_tasks(0)

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread is not current_thread():
            self._thread.join(timeout=max(1.0, self._interval + 0.25))
        self._state.set_background_tasks(0)


class CognitionWorker:
    def __init__(self, store, cognition, resources: ResourceGovernor, max_pending: int = 100,
                 shutdown_grace_seconds: float = 5, events: EventBus | None = None):
        self.store, self.cognition, self.resources = store, cognition, resources
        self.max_pending = max_pending
        self.shutdown_grace_seconds = shutdown_grace_seconds
        self.events = events
        self._wake = Event()
        self._stop = Event()
        self._thread: Thread | None = None
        self._lifecycle_lock = Lock()
        self._current_task: str | None = None
        self.last_error: str | None = None

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    @property
    def current_task(self) -> str | None:
        return self._current_task

    def start(self) -> int:
        # Repeated lifecycle calls must not create competing queue workers.
        with self._lifecycle_lock:
            if self.running:
                if self._stop.is_set():
                    raise RuntimeError("Cognition worker is still shutting down")
                return 0
            recovered = self.store.recover_cognition_tasks()
            self._stop.clear()
            self._thread = Thread(target=self._run, name="sylphy-cognition", daemon=True)
            self._thread.start()
            self._wake.set()
            return recovered

    def enqueue(self, turn: Turn) -> CognitionTask:
        task = self.store.enqueue_cognition_task(turn.session_id, (turn.user.id, turn.assistant.id), self.max_pending)
        if self.events:
            self.events.publish(EventType.COGNITION_TASK_QUEUED, {"task_id": task.id})
        self._wake.set()
        return task

    def _run(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(0.25)
            self._wake.clear()
            if self._stop.is_set():
                break
            task = self.store.claim_cognition_task()
            if task is None:
                continue
            self._current_task = task.id
            if self.events:
                self.events.publish(EventType.COGNITION_TASK_STARTED, {"task_id": task.id})
            try:
                messages = self.store.messages(task.source_session_id)
                wanted = set(task.source_message_ids)
                source = [message for message in messages if message.id in wanted]
                user = next(message for message in source if message.role == "user")
                assistant = next(message for message in source if message.role == "assistant")
                with self.resources.generation(cancel=self._stop, priority="cognition") as cancel:
                    self.cognition.analyze(Turn(task.source_session_id, user, assistant), messages,
                                           cancel=cancel)
                self.store.finish_cognition_task(task.id)
                # Worker diagnostics describe the latest task outcome, not a stale
                # failure from an earlier task that has since recovered.
                self.last_error = None
                if self.events:
                    self.events.publish(EventType.COGNITION_TASK_COMPLETED, {"task_id": task.id})
            except InterruptedError:
                self.store.defer_cognition_task(task.id)
                logger.info("Deferred cognition task after cancellation or conversation preemption: %s", task.id)
            except Exception as exc:
                logger.exception("Cognition task failed: %s", task.id)
                self.last_error = f"{type(exc).__name__}: {exc}"
                try:
                    self.store.finish_cognition_task(task.id, self.last_error)
                except Exception:
                    logger.exception("Could not persist cognition task failure: %s", task.id)
                if self.events:
                    self.events.publish(EventType.COGNITION_TASK_FAILED, {"task_id": task.id, "error": type(exc).__name__})
            finally:
                self._current_task = None

    def stop(self) -> bool:
        with self._lifecycle_lock:
            self._stop.set()
            self._wake.set()
            thread = self._thread
        if thread and thread is not current_thread():
            thread.join(timeout=self.shutdown_grace_seconds)
        return not self.running


class ExplorationWorker:
    """Event-woken, single-thread worker for bounded question formation."""

    def __init__(self, store, exploration: ExplorationService, resources: ResourceGovernor,
                 max_queue_items: int, cooldown_seconds: float, max_attempts_per_period: int,
                 period_seconds: float, expiry_days: int = 30, shutdown_grace_seconds: float = 5,
                 on_question: Callable[[ExplorationQuestion], None] | None = None):
        self.store = store
        self.exploration = exploration
        self.resources = resources
        self.max_queue_items = max_queue_items
        self.cooldown_seconds = cooldown_seconds
        self.max_attempts_per_period = max_attempts_per_period
        self.period_seconds = period_seconds
        self.expiry_days = expiry_days
        self.shutdown_grace_seconds = shutdown_grace_seconds
        self.on_question = on_question
        self._wake = Event()
        self._stop = Event()
        self._thread: Thread | None = None
        self._lifecycle_lock = Lock()
        self._current_task: str | None = None
        self.last_error: str | None = None

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    @property
    def current_task(self) -> str | None:
        return self._current_task

    def start(self) -> int:
        # Repeated lifecycle calls must not create competing queue workers.
        with self._lifecycle_lock:
            if self.running:
                if self._stop.is_set():
                    raise RuntimeError("Exploration worker is still shutting down")
                return 0
            recovered = self.store.recover_exploration_tasks(self.expiry_days)
            self._stop.clear()
            self._thread = Thread(target=self._run, name="sylphy-exploration", daemon=True)
            self._thread.start()
            self.wake()
            return recovered

    def enqueue(self, turn: Turn) -> ExplorationTask | None:
        task = self.store.enqueue_exploration_task(
            turn.session_id, (turn.user.id, turn.assistant.id), self.max_queue_items,
            self.cooldown_seconds, self.max_attempts_per_period, self.period_seconds,
        )
        if task is not None:
            self.wake()
        return task

    def wake(self) -> None:
        self._wake.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            self._wake.wait()
            self._wake.clear()
            if self._stop.is_set():
                break
            while not self._stop.is_set():
                task, wait_seconds = self.store.claim_exploration_task(
                    self.cooldown_seconds, self.max_attempts_per_period, self.period_seconds, self.expiry_days
                )
                if task is None:
                    if wait_seconds > 0:
                        # This is a single rate-limit wait, awakened early by new
                        # work, resource release, or shutdown; there is no poll loop.
                        self._wake.wait(wait_seconds)
                        self._wake.clear()
                        continue
                    break
                self._current_task = task.id
                try:
                    messages = self.store.messages(task.source_session_id)
                    wanted = set(task.source_message_ids)
                    source = [message for message in messages if message.id in wanted]
                    user = next(message for message in source if message.role == "user")
                    assistant = next(message for message in source if message.role == "assistant")
                    with self.resources.generation(cancel=self._stop, priority="exploration") as cancel:
                        question = self.exploration.analyze(
                            task, Turn(task.source_session_id, user, assistant), messages, cancel=cancel
                        )
                    if (question is not None and self.on_question is not None
                            and question.source_message_ids == task.source_message_ids):
                        try:
                            self.on_question(question)
                        except Exception:
                            # Delivery is best-effort and must not change persisted Exploration success.
                            logger.exception("Could not publish Presence for Exploration question %s", question.id)
                    self.store.finish_exploration_task(task.id)
                    # Clear stale worker error after a successful task.
                    self.last_error = None
                except InterruptedError:
                    self.store.defer_exploration_task(task.id)
                    if self._stop.is_set():
                        break
                except Exception as exc:
                    logger.exception("Exploration task failed: %s", task.id)
                    self.last_error = f"{type(exc).__name__}: {exc}"
                    try:
                        self.store.finish_exploration_task(task.id, self.last_error)
                    except Exception:
                        logger.exception("Could not persist Exploration task failure: %s", task.id)
                finally:
                    self._current_task = None

    def stop(self) -> bool:
        with self._lifecycle_lock:
            self._stop.set()
            self._wake.set()
            thread = self._thread
        if thread and thread is not current_thread():
            thread.join(timeout=self.shutdown_grace_seconds)
        return not self.running


class Runtime:
    _TRANSITIONS = {
        RuntimePhase.STOPPED: {RuntimePhase.STARTING},
        RuntimePhase.STARTING: {RuntimePhase.READY, RuntimePhase.ERROR, RuntimePhase.SHUTTING_DOWN},
        RuntimePhase.READY: {RuntimePhase.RUNNING, RuntimePhase.IDLE, RuntimePhase.SHUTTING_DOWN, RuntimePhase.ERROR},
        RuntimePhase.IDLE: {RuntimePhase.RUNNING, RuntimePhase.SHUTTING_DOWN, RuntimePhase.ERROR},
        RuntimePhase.RUNNING: {RuntimePhase.IDLE, RuntimePhase.SHUTTING_DOWN, RuntimePhase.ERROR},
        RuntimePhase.ERROR: {RuntimePhase.SHUTTING_DOWN, RuntimePhase.STARTING},
        RuntimePhase.SHUTTING_DOWN: {RuntimePhase.STOPPED, RuntimePhase.ERROR},
    }

    def __init__(self, model: str, max_concurrent_generations: int = 1,
                 heartbeat_enabled: bool = True, heartbeat_interval_seconds: float = 30,
                 presence_enabled: bool = False, presence_queue_capacity: int = 20):
        if max_concurrent_generations != 1:
            raise ValueError("Runtime currently supports exactly one concurrent generation")
        self.events = EventBus()
        self.state = RuntimeState(model)
        self.exploration_worker: ExplorationWorker | None = None
        self._exploration_store = None
        self.resources = ResourceGovernor(
            max_concurrent_generations,
            on_wait=lambda: self.events.publish(EventType.RESOURCE_LIMIT_REACHED,
                                                {"resource": "conversation_generation"}),
            on_release=self._wake_exploration,
        )
        self.heartbeat = Heartbeat(heartbeat_interval_seconds, self.events, self.state) if heartbeat_enabled else None
        self.presence = PresenceService(presence_enabled, presence_queue_capacity)
        self.interaction_policy = InteractionPolicy()
        self.companion_enabled = False
        self.autonomy_enabled = False
        self.initiative_enabled = False
        self.avatar: AvatarController | None = None
        self.voice_service: VoiceService | None = None
        self._lock = Lock()
        self._operation_condition = Condition(Lock())
        self._shutdown_timeout_seconds = 30.0
        self.cognition_worker: CognitionWorker | None = None
        self._investigation_store = None
        self._investigation_budget = InvestigationBudget()
        self._investigation_max_attempts = 3
        self._investigation_executor: InvestigationExecutor | None = None
        self._investigation_execution_lock = Lock()
        self._investigation_condition = Condition(Lock())
        self._active_investigation_id: str | None = None
        self._active_investigation_cancel: Event | None = None

    def _wake_exploration(self) -> None:
        worker = self.exploration_worker
        if worker is not None:
            worker.wake()

    def configure_cognition_worker(self, store, cognition, max_pending: int = 100,
                                   shutdown_grace_seconds: float = 5) -> None:
        self.cognition_worker = CognitionWorker(store, cognition, self.resources, max_pending,
                                                shutdown_grace_seconds, self.events)

    def configure_avatar(self, avatar: AvatarController) -> None:
        self.avatar = avatar

    def configure_voice(self, voice_service: "VoiceService") -> None:
        """Attach the opt-in voice worker to Runtime's application lifecycle."""
        self.voice_service = voice_service

    def set_presentation_state(self, state: PresentationState | str) -> bool:
        """Validate the bounded avatar activity state before crossing the runtime boundary."""
        selected = normalize_presentation_state(state)
        if selected is None:
            logger.warning("Ignoring unsupported avatar presentation state")
            return False
        avatar = self.avatar
        if avatar is None:
            return False
        try:
            return bool(avatar.set_state(selected))
        except Exception:
            logger.exception("Avatar state update failed; Runtime remains available")
            return False

    def configure_exploration_worker(self, store, exploration: ExplorationService, max_queue_items: int = 20,
                                     cooldown_seconds: float = 3600, max_attempts_per_period: int = 3,
                                     period_seconds: float = 86400, expiry_days: int = 30,
                                     shutdown_grace_seconds: float = 5) -> None:
        self._exploration_store = store
        self.exploration_worker = ExplorationWorker(store, exploration, self.resources, max_queue_items,
                                                    cooldown_seconds, max_attempts_per_period, period_seconds,
                                                    expiry_days, shutdown_grace_seconds,
                                                    self._publish_exploration_question)

    def _publish_exploration_question(self, question: ExplorationQuestion) -> bool:
        """Durably queue one opt-in Presence notice for an eligible saved question."""
        if self._exploration_store is None:
            return False
        if not isinstance(question, ExplorationQuestion) or question.status != "pending":
            return False
        saved = self._exploration_store.get_exploration_question(question.id)
        if saved is None or saved.status != "pending" or saved.expires_at <= datetime.now(timezone.utc):
            return False
        if not self._owns_exploration_source(saved):
            return False
        if self.companion_enabled:
            return self._publish_exploration_question_with_companion(saved)
        try:
            event = PresenceEvent(
                PresenceEventType.NOTICE,
                "A question to explore",
                saved.question,
                event_id=saved.id,
                created_at=saved.created_at,
                source_id="exploration",
                deduplication_key=saved.id,
            )
        except ValueError:
            # Preserve the stored question unchanged; it may not satisfy Presence's display contract.
            return False
        status = saved.notification_status
        decision = self.interaction_policy.decide(
            InteractionEvent(saved.id, "exploration", saved.created_at, saved.id, "presence", saved.question,
                             saved.expires_at),
            PolicyConditions(
                feature_enabled=self.presence.enabled,
                dismissed=saved.status == "dismissed" or status == "dismissed",
                duplicate=status in {"queued", "delivered"},
                expired=status == "expired" or saved.expires_at <= datetime.now(timezone.utc),
                queue_available=len(self.presence.pending()) < self.presence.capacity,
            ),
        )
        logger.debug("Interaction decision %s (%s) for event %s",
                     decision.action.value, decision.reason_code.value, decision.source_event_id)
        if decision.action is not InteractionAction.SPEAK:
            return False
        if not self._exploration_store.queue_exploration_notification(saved.id):
            return False
        event = PresenceEvent(
            event.event_type, event.title, decision.message or event.content,
            event_id=event.event_id, created_at=event.created_at,
            source_id=event.source_id, deduplication_key=event.deduplication_key,
        )
        if self.presence.publish(event):
            self.set_presentation_state(PresentationState.CURIOUS)
            return True
        self._exploration_store.release_exploration_notification(saved.id)
        return False

    def _publish_exploration_question_with_companion(
        self, saved: ExplorationQuestion
    ) -> bool:
        """Route one real presence event through the Companion Runtime pipeline.

        The event is a saved Exploration question that already exists durably.
        Policy decides whether it may be delivered; the delivery callback below
        is the only place a Presence notice is queued or published. Any failure
        is isolated: the persisted question and conversation remain untouched.
        """
        store = self._exploration_store
        if store is None:
            return False
        try:
            notice = PresenceEvent(
                PresenceEventType.NOTICE,
                "A question to explore",
                saved.question,
                event_id=saved.id,
                created_at=saved.created_at,
                source_id="exploration",
                deduplication_key=saved.id,
            )
        except ValueError:
            # Preserve the stored question unchanged; it may not satisfy Presence's display contract.
            return False

        now = datetime.now(timezone.utc)
        status = saved.notification_status
        queue_available = len(self.presence.pending()) < self.presence.capacity
        conditions = PolicyConditions(
            feature_enabled=self.presence.enabled,
            dismissed=saved.status == "dismissed" or status == "dismissed",
            duplicate=status in {"queued", "delivered"},
            expired=status == "expired" or saved.expires_at <= now,
            queue_available=queue_available,
        )
        try:
            event = presence_notice_event(
                notice_id=saved.id,
                occurred_at=now,
                source_session_id=saved.source_session_id,
                expires_at=saved.expires_at,
            )
            context = build_presence_context(event, queue_available=queue_available)
            decision = propose_presence_decision(event, context)
        except Exception:
            logger.exception(
                "Companion presence event assembly failed; no notice was published"
            )
            return False

        def execute() -> bool:
            if not store.queue_exploration_notification(saved.id):
                return False
            delivered_event = PresenceEvent(
                notice.event_type,
                notice.title,
                notice.content,
                event_id=notice.event_id,
                created_at=notice.created_at,
                source_id=notice.source_id,
                deduplication_key=notice.deduplication_key,
            )
            if self.presence.publish(delivered_event):
                self.set_presentation_state(PresentationState.CURIOUS)
                return True
            store.release_exploration_notification(saved.id)
            return False

        try:
            result = record_presence_event(
                store,
                event=event,
                context=context,
                decision=decision,
                conditions=conditions,
                occurred_at=now,
                execute=execute,
                pending_notice_count=lambda: len(self.presence.pending()),
                evidence=saved.id,
                companion_enabled=True,
            )
        except Exception:
            logger.exception(
                "Companion presence processing failed; no notice was published"
            )
            return False

        if not result.published:
            return False

        self._maybe_autonomously_execute(event, context, conditions)
        return True

    def _maybe_autonomously_execute(
        self,
        event: CompanionEvent,
        context: CompanionContext,
        conditions: PolicyConditions,
    ) -> bool:
        """Propose and, when permitted, run one bounded autonomous action.

        The trigger is a real delivered Presence event. The proposal is
        deterministic; the existing InteractionPolicy decides; the permission gate
        and bounded scope decide whether execution follows. Any failure is
        isolated: the delivery already succeeded and nothing is executed blindly.
        """
        if not self.autonomy_enabled or not self.companion_enabled:
            return False
        if not self.investigation_execution_enabled:
            return False
        try:
            autonomy_conditions = replace(conditions, feature_enabled=self.autonomy_enabled)
            decision = propose_autonomy_decision(
                event, context, conditions=autonomy_conditions, action_category="investigation"
            )
            if decision.proposed_action in (InteractionAction.SILENCE, InteractionAction.WAIT):
                return False
            autonomy_result = record_autonomy_execution(
                self._exploration_store,
                decision=decision,
                context=context,
                conditions=autonomy_conditions,
                execute=lambda action: self._execute_autonomy_action(action, decision),
                pending_notice_count=lambda: len(self.presence.pending()),
                companion_enabled=self.companion_enabled,
                policy=CompanionPolicy(self.interaction_policy),
            )
            if not autonomy_result.executed or autonomy_result.outcome.status is not OutcomeStatus.SUCCEEDED:
                return False
            action = autonomy_result.action
            question_id = (
                dict(action.parameters or {}) if action is not None else {}
            ).get("question_id")
            if not question_id:
                return False
            investigation = (
                self._investigation_store.get_investigation_for_question(question_id)
                if self._investigation_store is not None
                else None
            )
            if investigation is None or investigation.status is not InvestigationStatus.COMPLETED:
                return False
            return self._maybe_initiate_investigation(investigation.id)
        except Exception:
            logger.exception(
                "Autonomous action evaluation failed; no blind execution followed"
            )
            return False

    def _execute_autonomy_action(self, action: Action, decision: AutonomyDecision) -> Investigation:
        """Run the bounded autonomous action: one investigation pass on one question."""
        parameters = dict(action.parameters or {})
        question_id = parameters.get("question_id")
        if not question_id:
            raise RuntimeError("Autonomous action did not carry a bounded investigation target")
        investigation = self._investigation_store.get_investigation_for_question(question_id)
        if investigation is not None:
            if investigation.status is not InvestigationStatus.QUEUED:
                raise RuntimeError("Autonomous action requires a queued investigation")
            scope = {str(key): str(value) for key, value in (decision.scope or {}).items()}
            max_pages = int(scope.get("max_pages") or 0)
            max_searches = int(scope.get("max_search_requests") or 0)
            max_models = int(scope.get("max_model_calls") or 0)
            max_sources = int(scope.get("max_source_bytes") or 0)
            max_chars = int(scope.get("max_extracted_text_chars") or 0)
            if (
                (max_pages and investigation.pages_read >= max_pages)
                or (max_searches and investigation.search_calls_used >= max_searches)
                or (max_models and investigation.model_calls_used >= max_models)
                or (max_sources and investigation.source_bytes_used >= max_sources)
                or (max_chars and investigation.extracted_chars_used >= max_chars)
            ):
                raise RuntimeError("Autonomous action exceeds its bounded scope")
        return self.start_investigation(question_id, allow_external_search=True)

    def set_autonomy_enabled(self, enabled: bool) -> None:
        """Enable or disable the bounded autonomous action layer."""
        if not isinstance(enabled, bool):
            raise ValueError("autonomy_enabled must be a boolean")
        self.autonomy_enabled = enabled

    @property
    def autonomy_execution_enabled(self) -> bool:
        return self.autonomy_enabled and self.investigation_execution_enabled

    def set_initiative_enabled(self, enabled: bool) -> None:
        """Enable or disable the bounded purposeful-initiative layer."""
        if not isinstance(enabled, bool):
            raise ValueError("initiative_enabled must be a boolean")
        self.initiative_enabled = enabled

    @property
    def initiative_execution_enabled(self) -> bool:
        return self.initiative_enabled and self.companion_enabled and self.presence.enabled

    def _maybe_initiate_investigation(self, investigation_id: str) -> bool:
        """Evaluate whether one completed bounded investigation warrants an initiative.

        The trigger is a real, durably completed bounded investigation. The
        proposal is deterministic; the existing InteractionPolicy decides whether
        the resulting response may be delivered; permission and scope decide
        whether delivery follows. Any failure is isolated: no blind delivery
        happens and the conversation/investigation remain untouched.
        """
        if not self.initiative_enabled or not self.companion_enabled:
            return False
        if not self._investigation_store or not self.presence.enabled or not self.presence.started:
            return False
        investigation = self._investigation_store.get_investigation(investigation_id)
        if investigation is None or investigation.status is not InvestigationStatus.COMPLETED:
            return False
        result = self._investigation_store.get_investigation_result(investigation_id)
        now = datetime.now(timezone.utc)
        event = initiative_trigger_event(investigation_id, occurred_at=now, expires_at=investigation.expires_at)
        context = build_initiative_context(event, investigation_id=investigation_id, result=result)
        conditions = PolicyConditions(
            feature_enabled=self.initiative_enabled,
            permission_granted=True,
            permission_denied=False,
            dismissed=False,
            duplicate=False,
            expired=event.expires_at is not None and event.expires_at <= now,
            cooldown_active=False,
            queue_available=len(self.presence.pending()) < self.presence.capacity,
            resource_available=True,
            grounded=bool(result is not None and result.evidence_sufficient),
            relevant=bool(result is not None and result.evidence_sufficient),
            needs_context=False,
            request_permission=False,
            can_wait=False,
            requested_action=InteractionAction.SPEAK,
            not_before=None,
        )
        try:
            decision = propose_initiative_decision(event, context, conditions=conditions, result=result)
            content = (decision.response or (result.summary.strip() if result is not None else None) or "").strip()
            content = content[:1000] or None
            if not content:
                return False

            def execute(action: Action) -> bool:
                notice = PresenceEvent(
                    PresenceEventType.NOTICE,
                    "Research finding",
                    content,
                    event_id=investigation_id,
                    created_at=now,
                    source_id="initiative",
                    deduplication_key=f"initiative-{investigation_id}",
                )
                return self.presence.publish(notice)

            initiative_result = record_initiative(
                self._exploration_store,
                decision=decision,
                context=context,
                conditions=conditions,
                execute=execute,
                pending_notice_count=lambda: len(self.presence.pending()),
                companion_enabled=self.companion_enabled,
                policy=CompanionPolicy(self.interaction_policy),
            )
            return bool(initiative_result.delivered)
        except Exception:
            logger.exception(
                "Bounded initiative evaluation failed; no blind delivery followed"
            )
            return False


    def _owns_exploration_source(self, question: ExplorationQuestion) -> bool:
        if self._exploration_store is None or len(question.source_message_ids) != 2:
            return False
        wanted = set(question.source_message_ids)
        source = [message for message in self._exploration_store.messages(question.source_session_id)
                  if message.id in wanted]
        return (len(source) == 2 and {message.id for message in source} == wanted
                and {message.role for message in source} == {"user", "assistant"}
                and all(message.session_id == question.source_session_id for message in source))

    def restore_pending_presence(self) -> int:
        """Rebuild the in-memory Presence projection from durable queued notices."""
        store = self._exploration_store
        if store is None or not self.presence.enabled or not self.presence.started:
            return 0
        restored = 0
        for question in store.list_queued_exploration_notifications():
            if not self._owns_exploration_source(question):
                store.dismiss_exploration_notification(question.id)
                continue
            try:
                event = PresenceEvent(
                    PresenceEventType.NOTICE, "A question to explore", question.question,
                    event_id=question.id, created_at=question.created_at,
                    source_id="exploration", deduplication_key=question.id,
                )
            except ValueError:
                store.dismiss_exploration_notification(question.id)
                continue
            if not self.presence.publish(event):
                break
            restored += 1
        return restored

    def set_presence_enabled(self, enabled: bool) -> None:
        self.presence.set_enabled(enabled)
        if enabled:
            self.restore_pending_presence()

    def deliver_presence(self, event: PresenceEvent) -> bool:
        """Persist Exploration delivery before removing and printing its notice."""
        if not self.presence.enabled or not self.presence.started:
            return False
        if event.source_id == "exploration" and event.deduplication_key:
            store = self._exploration_store
            if store is None:
                return False
            try:
                delivered = store.mark_exploration_notification_delivered(event.deduplication_key)
            except Exception:
                logger.exception("Could not persist Exploration notice delivery")
                return False
            self.presence.dismiss(event.event_id)
            return delivered
        return self.presence.dismiss(event.event_id)

    def dismiss_presence(self, event_id: str) -> bool:
        event = next((item for item in self.presence.pending() if item.event_id == event_id), None)
        if event is None:
            return False
        if event.source_id == "exploration" and event.deduplication_key:
            store = self._exploration_store
            if store is None or not store.dismiss_exploration_notification(event.deduplication_key):
                return False
        return self.presence.dismiss(event_id)

    def dismiss_exploration_question(self, question_id: str, store=None) -> ExplorationQuestion:
        repository = store or self._exploration_store
        if repository is None:
            raise RuntimeError("Exploration is disabled")
        question = repository.dismiss_exploration_question(question_id)
        for event in self.presence.pending():
            if event.source_id == "exploration" and event.deduplication_key == question_id:
                self.presence.dismiss(event.event_id)
        return question

    def configure_investigations(self, store, budget: InvestigationBudget,
                                 max_attempts: int = 3) -> None:
        """Enable durable investigation management; this configures no worker or inference."""
        if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or max_attempts < 1:
            raise ValueError("investigation max_attempts must be a positive integer")
        self._investigation_store = store
        self._investigation_budget = budget
        self._investigation_max_attempts = max_attempts

    def configure_investigation_execution(self, store, budget: InvestigationBudget, max_attempts: int,
                                          search_provider, web_reader, provider) -> None:
        """Enable explicit one-pass execution using existing web/model providers."""
        self.configure_investigations(store, budget, max_attempts)

        def analyze(messages, cancel):
            output_size = 0

            def check_output(part: str) -> None:
                nonlocal output_size
                output_size += len(part)
                if output_size > 20_000:
                    raise ValueError("Investigation analysis output exceeded its size limit")

            try:
                with self.resources.generation(cancel=cancel, priority="investigation") as effective_cancel:
                    return provider.reply(messages, cancel=effective_cancel, on_chunk=check_output)
            except InterruptedError:
                if cancel is not None and cancel.is_set():
                    raise
                raise InvestigationPreempted("Investigation yielded to a foreground inference") from None

        self._investigation_executor = InvestigationExecutor(store, search_provider, web_reader, analyze)

    @property
    def investigations_enabled(self) -> bool:
        return self._investigation_store is not None

    @property
    def investigation_execution_enabled(self) -> bool:
        return self._investigation_executor is not None

    def queue_investigation(self, question_id: str) -> Investigation:
        if self._investigation_store is None:
            raise RuntimeError("Investigation management is disabled")
        return self._investigation_store.create_investigation(
            question_id, self._investigation_budget, self._investigation_max_attempts
        )

    def list_investigations(self, status: InvestigationStatus | None = None) -> list[Investigation]:
        if self._investigation_store is None:
            raise RuntimeError("Investigation management is disabled")
        return self._investigation_store.list_investigations(status)

    def get_investigation(self, investigation_id: str) -> Investigation | None:
        if self._investigation_store is None:
            raise RuntimeError("Investigation management is disabled")
        return self._investigation_store.get_investigation(investigation_id)

    def get_investigation_for_question(self, question_id: str) -> Investigation | None:
        if self._investigation_store is None:
            raise RuntimeError("Investigation management is disabled")
        return self._investigation_store.get_investigation_for_question(question_id)

    def get_exploration_question(self, question_id: str):
        if self._investigation_store is None:
            raise RuntimeError("Investigation management is disabled")
        return self._investigation_store.get_exploration_question(question_id)

    def get_investigation_evidence(self, investigation_id: str):
        if self._investigation_store is None:
            raise RuntimeError("Investigation management is disabled")
        return self._investigation_store.list_investigation_evidence(investigation_id)

    def get_investigation_result(self, investigation_id: str):
        if self._investigation_store is None:
            raise RuntimeError("Investigation management is disabled")
        return self._investigation_store.get_investigation_result(investigation_id)

    def cancel_investigation(self, investigation_id: str) -> Investigation:
        if self._investigation_store is None:
            raise RuntimeError("Investigation management is disabled")
        with self._investigation_condition:
            cancel = (self._active_investigation_cancel
                      if self._active_investigation_id == investigation_id else None)
        try:
            item = self._investigation_store.cancel_investigation(investigation_id)
        except ValueError:
            item = self._investigation_store.get_investigation(investigation_id)
            if item is None:
                raise KeyError(f"Investigation not found: {investigation_id}")
            if item.status != InvestigationStatus.CANCELLED:
                raise
        if cancel is not None:
            cancel.set()
        return item

    def start_investigation(self, question_id: str, *, allow_external_search: bool = False) -> Investigation:
        if self._investigation_store is None or self._investigation_executor is None:
            raise RuntimeError("Investigation execution requires Investigation, Web Search, and Web Reader enabled")
        if allow_external_search is not True:
            raise RuntimeError("Starting an investigation requires explicit confirmation that its question may be sent to Search")
        if not self._investigation_execution_lock.acquire(blocking=False):
            raise RuntimeError("An investigation is already executing")
        cancel = Event()
        try:
            with self._investigation_condition:
                if self.state.snapshot().phase in {
                    RuntimePhase.SHUTTING_DOWN, RuntimePhase.STOPPED, RuntimePhase.ERROR
                }:
                    raise RuntimeError("Runtime is not accepting investigation execution")
                # Register before SQLite work so concurrent shutdown cannot miss this operation.
                self._active_investigation_id = "<starting>"
                self._active_investigation_cancel = cancel
            item = self.queue_investigation(question_id)
            if item.status != InvestigationStatus.QUEUED:
                return item
            with self._investigation_condition:
                self._active_investigation_id = item.id
                self._investigation_condition.notify_all()
            return self._investigation_executor.execute(item, cancel)
        finally:
            with self._investigation_condition:
                self._active_investigation_id = None
                self._active_investigation_cancel = None
                self._investigation_condition.notify_all()
            self._investigation_execution_lock.release()

    def transition(self, target: RuntimePhase) -> None:
        current = self.state.snapshot().phase
        if target not in self._TRANSITIONS[current]:
            raise RuntimeError(f"Invalid runtime transition: {current.value} -> {target.value}")
        self.state.set_phase(target)
        logger.info("Runtime state transition %s -> %s", current.value, target.value)
        self.events.publish(EventType.RUNTIME_STATE_CHANGED, {"from": current.value, "to": target.value})

    def start(self, initialize: Callable[[], None] | None = None) -> None:
        self.transition(RuntimePhase.STARTING)
        self.events.publish(EventType.RUNTIME_STARTED)
        try:
            if initialize:
                initialize()
            if self.avatar:
                self.avatar.start()
            if self._investigation_store:
                recovered = self._investigation_store.recover_investigations()
                if recovered:
                    logger.warning("Recovered %d interrupted investigations", recovered)
            self.presence.start()
            self.restore_pending_presence()
            if self.heartbeat:
                self.heartbeat.start()
            if self.cognition_worker:
                recovered = self.cognition_worker.start()
                if recovered:
                    logger.warning("Recovered %d interrupted cognition tasks", recovered)
            if self.exploration_worker:
                recovered = self.exploration_worker.start()
                if recovered:
                    logger.warning("Recovered %d interrupted Exploration tasks", recovered)
            self.transition(RuntimePhase.READY)
            self.events.publish(EventType.RUNTIME_READY)
            logger.info("Runtime ready")
        except Exception as exc:
            self.state.record_error(exc)
            self.transition(RuntimePhase.ERROR)
            logger.exception("Runtime startup failed")
            raise

    def change_session(self, session_id: str | None) -> None:
        current = self.state.snapshot()
        self.state.set_session(session_id)
        if current.phase == RuntimePhase.READY:
            self.transition(RuntimePhase.IDLE)
        self.events.publish(EventType.SESSION_CHANGED, {"session_id": session_id})
        self.set_presentation_state(PresentationState.IDLE)

    def memory_operation(self, operation: str, memory_id: str | None = None) -> None:
        self.state.touch()
        self.events.publish(EventType.MEMORY_OPERATION, {"operation": operation, "memory_id": memory_id})

    def publish_presence(self, event: PresenceEvent) -> bool:
        """Accept an internal Presence event without doing work on the caller thread."""
        return self.presence.publish(event)

    def run_conversation(self, service: ConversationService, session_id: str, text: str, cancel: Event | None = None,
                         on_chunk: Callable[[str], None] | None = None, cognition=None, persistence=None) -> Turn:
        self.state.touch()
        if self.state.snapshot().session_id != session_id:
            self.change_session(session_id)
        self.events.publish(EventType.USER_MESSAGE_RECEIVED, {"session_id": session_id})
        operation_started = False
        try:
            with self.resources.generation(cancel):
                self.state.set_session(session_id)
                self.transition(RuntimePhase.RUNNING)
                self.state.begin_operation("generation")
                operation_started = True
                with self._operation_condition:
                    self._operation_condition.notify_all()
                self.events.publish(EventType.RESPONSE_STARTED, {"session_id": session_id})
                self.set_presentation_state(PresentationState.THINKING)

                def stream_chunk(chunk: str) -> None:
                    self.set_presentation_state(PresentationState.SPEAKING)
                    if on_chunk is not None:
                        on_chunk(chunk)

                try:
                    result = service.respond(session_id, text, cancel=cancel,
                                             on_chunk=stream_chunk if on_chunk is not None else None)
                except ConversationCancelled:
                    self.events.publish(EventType.RESPONSE_CANCELLED, {"session_id": session_id})
                    raise
                except InterruptedError as exc:
                    self.state.record_error(exc)
                    self.events.publish(EventType.RESPONSE_CANCELLED, {"session_id": session_id})
                    raise
                except Exception as exc:
                    self.state.record_error(exc)
                    logger.error("Conversation operation failed (%s)", type(exc).__name__)
                    self.events.publish(EventType.MODEL_ERROR, {"error": type(exc).__name__})
                    raise
                self.events.publish(EventType.RESPONSE_COMPLETED, {"session_id": session_id})
                # ConversationService returns only after it has persisted the
                # completed turn. Enqueue is deliberately best-effort afterward.
                if self.cognition_worker:
                    try:
                        self.cognition_worker.enqueue(result)
                    except Exception:
                        logger.exception("Could not enqueue cognition after completed turn")
                if self.exploration_worker:
                    try:
                        self.exploration_worker.enqueue(result)
                    except Exception:
                        logger.exception("Could not enqueue Exploration after completed turn")
                if self.companion_enabled and persistence is not None:
                    # Companion bookkeeping is optional and must never fail the
                    # conversation. The turn is already durably persisted here.
                    try:
                        record_completed_turn(
                            persistence,
                            session_id=session_id,
                            turn=result,
                            occurred_at=datetime.now(timezone.utc),
                            pending_presence_notices=len(self.presence.pending()),
                        )
                    except Exception:
                        logger.exception(
                            "Companion bookkeeping failed; conversation turn remains valid"
                        )
                elif not self.companion_enabled:
                    logger.debug(
                        "Companion feature disabled; no turn bookkeeping for session %s",
                        session_id,
                    )
                elif persistence is None:
                    logger.debug(
                        "Companion bookkeeping skipped: no persistence store provided"
                    )
                return result
        except InterruptedError:
            if not operation_started:
                self.events.publish(EventType.RESPONSE_CANCELLED, {"session_id": session_id})
            raise
        finally:
            if operation_started:
                self.set_presentation_state(PresentationState.IDLE)
                self.state.end_operation()
                with self._operation_condition:
                    self._operation_condition.notify_all()
                if self.state.snapshot().phase == RuntimePhase.RUNNING:
                    self.transition(RuntimePhase.IDLE)

    def run_vision_conversation(self, service: ConversationService, session_id: str, question: str,
                                camera, vision_provider, cancel: Event | None = None,
                                on_chunk: Callable[[str], None] | None = None,
                                on_capture_state: Callable[[bool], None] | None = None) -> Turn:
        """Capture and analyze one frame, then persist one ordinary conversation turn.

        Vision and conversation each acquire the single inference slot separately. The transient
        observation is passed only to this ConversationService call; this route does not enqueue
        duplicate Cognition or Exploration work.
        """
        clean_question = question.strip()
        if not clean_question:
            raise ValueError("Visual question cannot be empty")
        if len(clean_question) > MAX_QUESTION_CHARS:
            raise ValueError(f"Visual question must be at most {MAX_QUESTION_CHARS} characters")
        camera_config = getattr(camera, "config", None)
        if camera_config is None or not camera_config.enabled:
            raise ValueError("Vision is disabled. Set [vision].enabled = true to opt in.")
        configured_model = camera_config.model.strip()
        provider_model = getattr(vision_provider, "model", configured_model).strip()
        if not configured_model or provider_model != configured_model:
            raise ValueError("Vision model is not configured consistently; no fallback is used.")
        # Model metadata is checked before camera activation so missing/unsupported models fail closed.
        vision_provider.ensure_available()
        if self.state.snapshot().session_id != session_id:
            self.change_session(session_id)
        self.state.touch()
        self.state.set_session(session_id)
        self.events.publish(EventType.USER_MESSAGE_RECEIVED, {"session_id": session_id})
        operation_started = False
        camera_active_notified = False
        try:
            self.transition(RuntimePhase.RUNNING)
            self.state.begin_operation("vision")
            operation_started = True
            with self._operation_condition:
                self._operation_condition.notify_all()
            self.events.publish(EventType.RESPONSE_STARTED, {"session_id": session_id, "source": "vision"})
            self.set_presentation_state(PresentationState.OBSERVING)
            if on_capture_state is not None:
                on_capture_state(True)
                camera_active_notified = True
            try:
                image_jpeg, device_id = camera.capture_one()
            finally:
                if camera_active_notified and on_capture_state is not None:
                    try:
                        on_capture_state(False)
                    finally:
                        camera_active_notified = False
                self.set_presentation_state(PresentationState.THINKING)
            if cancel and cancel.is_set():
                raise InterruptedError("Vision conversation cancelled")
            # The visual inference holds the shared slot only for its own request.
            with self.resources.priority_reservation("conversation", cancel) as reservation:
                with self.resources.generation(cancel=cancel, priority="vision", reservation=reservation,
                                               retain_reservation=True) as slot_cancel:
                    observation = vision_provider.analyze(image_jpeg, clean_question, device_id,
                                                          cancel=slot_cancel)
                del image_jpeg
                if cancel and cancel.is_set():
                    raise InterruptedError("Vision conversation cancelled")
                self.state.set_session(session_id)

                def stream_chunk(chunk: str) -> None:
                    self.set_presentation_state(PresentationState.SPEAKING)
                    if on_chunk is not None:
                        on_chunk(chunk)

                # Vision releases inference; the foreground reservation prevents background work
                # taking the slot before the ordinary conversation response acquires it.
                with self.resources.generation(cancel=cancel, priority="conversation",
                                               reservation=reservation):
                    result = service.respond(session_id, clean_question, cancel=cancel,
                                             on_chunk=stream_chunk if on_chunk is not None else None,
                                             temporary_context=observation)
            self.events.publish(EventType.RESPONSE_COMPLETED, {"session_id": session_id, "source": "vision"})
            return result
        except ConversationCancelled:
            self.events.publish(EventType.RESPONSE_CANCELLED, {"session_id": session_id})
            raise
        except InterruptedError as exc:
            self.state.record_error(exc)
            self.events.publish(EventType.RESPONSE_CANCELLED, {"session_id": session_id})
            raise
        except Exception as exc:
            self.state.record_error(exc)
            logger.error("Vision conversation failed (%s)", type(exc).__name__)
            self.events.publish(EventType.MODEL_ERROR, {"error": type(exc).__name__})
            raise
        finally:
            if camera_active_notified and on_capture_state is not None:
                try:
                    on_capture_state(False)
                except Exception:
                    logger.exception("Could not clear Vision capture indicator")
            if operation_started:
                self.set_presentation_state(PresentationState.IDLE)
                self.state.end_operation()
                with self._operation_condition:
                    self._operation_condition.notify_all()
                if self.state.snapshot().phase == RuntimePhase.RUNNING:
                    self.transition(RuntimePhase.IDLE)

    def run_vision_devices(self, camera, on_capture_state: Callable[[bool], None] | None = None) -> list[int]:
        """Explicitly probe a bounded set of camera indices and release each probe handle."""
        if not camera.config.enabled:
            raise ValueError("Vision is disabled. Set [vision].enabled = true to opt in.")
        self.state.touch()
        operation_started = False
        indicator_started = False
        try:
            self.transition(RuntimePhase.RUNNING)
            self.state.begin_operation("vision_devices")
            operation_started = True
            with self._operation_condition:
                self._operation_condition.notify_all()
            self.set_presentation_state(PresentationState.OBSERVING)
            if on_capture_state is not None:
                on_capture_state(True)
                indicator_started = True
            return camera.devices()
        finally:
            if indicator_started and on_capture_state is not None:
                try:
                    on_capture_state(False)
                except Exception:
                    logger.exception("Could not clear Vision device-probe indicator")
            if operation_started:
                self.set_presentation_state(PresentationState.IDLE)
                self.state.end_operation()
                with self._operation_condition:
                    self._operation_condition.notify_all()
                if self.state.snapshot().phase == RuntimePhase.RUNNING:
                    self.transition(RuntimePhase.IDLE)

    def shutdown(self) -> None:
        with self._lock:
            phase = self.state.snapshot().phase
            if phase == RuntimePhase.STOPPED:
                return
            if phase != RuntimePhase.SHUTTING_DOWN:
                self.transition(RuntimePhase.SHUTTING_DOWN)
                self.events.publish(EventType.RUNTIME_SHUTTING_DOWN)
            try:
                with self._investigation_condition:
                    if self._active_investigation_cancel is not None:
                        self._active_investigation_cancel.set()
                self.presence.stop()
                # Stop explicitly activated microphone access before closing the avatar surface.
                voice_stopped = self.voice_service.stop() if self.voice_service else True
                if self.avatar:
                    try:
                        self.avatar.stop()
                    except Exception:
                        logger.exception("Avatar shutdown failed; Runtime shutdown continues")
                # Stop periodic work even when cognition later exceeds its bounded
                # grace period. A timed-out worker keeps the runtime in ERROR.
                if self.heartbeat:
                    self.heartbeat.stop()
                exploration_stopped = self.exploration_worker.stop() if self.exploration_worker else True
                cognition_stopped = self.cognition_worker.stop() if self.cognition_worker else True
                if not voice_stopped or not exploration_stopped or not cognition_stopped:
                    worker_name = ("Voice" if not voice_stopped else
                                   "Exploration" if not exploration_stopped else "Cognition")
                    error = TimeoutError(f"{worker_name} worker exceeded shutdown grace period")
                    self.state.record_error(error)
                    self.transition(RuntimePhase.ERROR)
                    raise error
                deadline = datetime.now(timezone.utc).timestamp() + self._shutdown_timeout_seconds
                with self._investigation_condition:
                    while self._active_investigation_id is not None:
                        remaining = deadline - datetime.now(timezone.utc).timestamp()
                        if remaining <= 0:
                            error = TimeoutError("Active investigation did not finish before runtime shutdown timeout")
                            self.state.record_error(error)
                            self.transition(RuntimePhase.ERROR)
                            raise error
                        self._investigation_condition.wait(remaining)
                with self._operation_condition:
                    while self.state.snapshot().active_operation is not None:
                        remaining = deadline - datetime.now(timezone.utc).timestamp()
                        if remaining <= 0:
                            error = TimeoutError("Active operation did not finish before runtime shutdown timeout")
                            self.state.record_error(error)
                            self.transition(RuntimePhase.ERROR)
                            raise error
                        self._operation_condition.wait(remaining)
                if self._investigation_store:
                    try:
                        self._investigation_store.defer_running_investigations()
                    except Exception as exc:
                        self.state.record_error(exc)
                        self.transition(RuntimePhase.ERROR)
                        raise
                self.events.publish(EventType.RUNTIME_STOPPED)
                self.transition(RuntimePhase.STOPPED)
                logger.info("Runtime stopped")
            finally:
                self.events.clear()
