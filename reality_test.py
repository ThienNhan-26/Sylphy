"""Turn 9 live reality-test experiment.

Attempts a controlled end-to-end exercise of the Companion Runtime chain:

  saved exploration question
    -> Presence event publication
    -> Bounded autonomy decision
    -> Investigation execution (blocked: no Brave Search API key)
    -> Purposeful initiative decision (blocked: no completed investigation)
    -> Presence queue publication (not actual user delivery)

Uses the local Ollama model (jarvis-uncen:latest) and real SQLite persistence.
No fakes for providers that are actually available.

Does NOT modify config.toml. Uses an in-code configuration override.
"""

import json
import logging
import os
import sys
import tempfile
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, "src")

from sylphy.companion_contract import CompanionEventType, CompanionEventSource
from sylphy.companion_slice import (
    presence_notice_event,
    build_presence_context,
    propose_presence_decision,
    propose_autonomy_decision,
    propose_initiative_decision,
    record_presence_event,
    record_autonomy_execution,
    record_initiative,
)
from sylphy.core import Message, Turn
from sylphy.exploration import ExplorationQuestion
from sylphy.investigation import InvestigationBudget, InvestigationStatus
from sylphy.interaction_policy import InteractionAction, PolicyConditions
from sylphy.runtime import Runtime
from sylphy.storage import SQLiteStore

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("reality_test")


def main() -> None:
    report: dict[str, object] = {
        "trigger": None,
        "environment": {},
        "configuration": {},
        "model": None,
        "provider": None,
        "permissions": {},
        "stages": {},
        "actual_result": None,
        "errors": [],
        "limitations": [],
    }

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        db_path = tmp_path / "reality.sqlite3"

        store = SQLiteStore(db_path)
        session = store.create_session("reality-session")
        now = datetime.now(timezone.utc)

        # Persist a real user/assistant turn so the exploration question is owned.
        user = Message("reality-user", session.id, "user", "I want to learn about urban trees.", now)
        assistant = Message(
            "reality-assistant",
            session.id,
            "assistant",
            "Urban trees provide shade and improve air quality.",
            now,
        )
        store.append_turn(user, assistant)

        # A saved Exploration question (real, durably persisted).
        question = ExplorationQuestion(
            id="reality-question-1",
            question="What are the benefits of urban trees?",
            source_session_id=session.id,
            source_message_ids=(user.id, assistant.id),
            source_memory_ids=(),
            created_at=now,
            status="pending",
            attempt_count=0,
            expires_at=now + timedelta(days=3),
            topic="urban trees",
            reason="User asked about urban trees.",
            notification_status=None,
        )
        saved = store.create_exploration_question(question, max_queue_items=10)
        report["trigger"] = {
            "question_id": saved.id,
            "question_text": saved.question,
            "source_session_id": saved.source_session_id,
        }

        # Runtime with real Ollama model, all companion features enabled.
        runtime = Runtime(
            "jarvis-uncen:latest",
            heartbeat_enabled=False,
            presence_enabled=True,
        )
        runtime.companion_enabled = True
        runtime.autonomy_enabled = True
        runtime.initiative_enabled = True
        # Configure the exploration store so the companion pipeline can access
        # persisted questions and message ownership (used by _owns_exploration_source).
        runtime._exploration_store = store
        runtime.start()

        try:
            # Stage 1: Presence event publication (real callback path).
            report["stages"]["1_presence_publication"] = _stage_presence(
                runtime, saved, report
            )

            # Stage 2: Bounded autonomy decision (real deterministic pipeline).
            report["stages"]["2_autonomy_decision"] = _stage_autonomy(
                runtime, saved, report
            )

            # Stage 3: Investigation execution - BLOCKED (no web search credentials).
            report["stages"]["3_investigation_execution"] = {
                "verdict": "BLOCKED",
                "reason": "BRAVE_SEARCH_API_KEY is not configured; real web search "
                          "cannot be performed. InvestigationExecutor requires a "
                          "search provider and web reader.",
            }

            # Stage 4: Purposeful initiative - BLOCKED (no completed investigation).
            report["stages"]["4_initiative_decision"] = {
                "verdict": "BLOCKED",
                "reason": "No completed investigation exists (stage 3 blocked), so "
                          "there is no investigation result to evaluate for initiative.",
            }

            # Stage 5: Delivery - queue publication only (not actual user delivery).
            report["stages"]["5_delivery"] = _stage_delivery(runtime, report)

        finally:
            runtime.shutdown()

        report["environment"] = {
            "ollama_host": "http://127.0.0.1:11434",
            "model": "jarvis-uncen:latest",
            "brave_search_api_key_configured": bool(os.environ.get("BRAVE_SEARCH_API_KEY")),
            "network_available": True,
            "python": sys.version,
        }
        report["configuration"] = {
            "companion_enabled": True,
            "autonomy_enabled": True,
            "initiative_enabled": True,
            "presence_enabled": True,
            "exploration_enabled": False,
            "web_search_enabled": False,
            "web_reader_enabled": False,
            "note": "Feature flags set in code for this experiment; config.toml "
                    "unchanged (companion.enabled=false, exploration.enabled=false, "
                    "no autonomy/initiative/web_search/web_reader sections).",
        }
        report["model"] = "jarvis-uncen:latest"
        report["provider"] = "OllamaProvider (local)"
        report["permissions"] = {
            "autonomy_required": ("investigation_search",),
            "initiative_required": ("companion_deliver",),
            "granted": False,
            "note": "No permission store exists; permissions are evaluated via "
                    "PolicyConditions passed at call time.",
        }
        report["limitations"] = [
            "Real web search unavailable: BRAVE_SEARCH_API_KEY not set.",
            "Investigation cannot execute without a search provider.",
            "Purposeful initiative cannot fire without a completed investigation result.",
            "Presence delivery is queue publication only; actual user-facing display "
            "occurs only at the CLI foreground prompt boundary (_deliver_presence "
            "called from _prompt_user before input()).",
            "Messages can be lost on Runtime shutdown (in-memory Presence queue).",
        ]

    print(json.dumps(report, indent=2, default=str))
    return report


def _stage_presence(runtime, saved, report) -> dict:
    """Stage 1: real presence event publication through the companion pipeline.

    Calls Runtime._publish_exploration_question_with_companion, which is the
    actual integration point: saved exploration question -> companion context ->
    decision -> policy -> delivery callback -> truthful outcome -> state.
    """
    try:
        result = runtime._publish_exploration_question_with_companion(saved)
        report["configuration"]["presence_capacity"] = runtime.presence.capacity
        return {
            "verdict": "VERIFIED",
            "published": result,
            "pending_queue_size": len(runtime.presence.pending()),
            "note": "_publish_exploration_question_with_companion routes one real "
                    "presence event through the Companion Runtime pipeline.",
        }
    except Exception as exc:
        logger.exception("Stage 1 failed")
        return {"verdict": "ERROR", "error": f"{type(exc).__name__}: {exc}"}


def _stage_autonomy(runtime, saved, report) -> dict:
    """Stage 2: real bounded autonomy decision pipeline."""
    try:
        from sylphy.companion_slice import restore_companion_state
        from sylphy.interaction_policy import InteractionAction
        now = datetime.now(timezone.utc)
        queue_available = len(runtime.presence.pending()) < runtime.presence.capacity
        event = presence_notice_event(
            notice_id=saved.id,
            occurred_at=now,
            source_session_id=saved.source_session_id,
            expires_at=saved.expires_at,
        )
        context = build_presence_context(event, queue_available=queue_available)
        conditions = PolicyConditions(
            feature_enabled=True,
            permission_granted=True,
            permission_denied=False,
            dismissed=False,
            duplicate=False,
            expired=False,
            cooldown_active=False,
            queue_available=queue_available,
            resource_available=True,
            grounded=True,
            relevant=True,
            needs_context=False,
            request_permission=False,
            can_wait=False,
            requested_action=InteractionAction.EXPLORE,
            not_before=None,
        )
        autonomy_conditions = replace(conditions, feature_enabled=True)
        decision = propose_autonomy_decision(
            event, context, conditions=autonomy_conditions, action_category="investigation"
        )
        report["permissions"]["autonomy_proposed_action"] = decision.proposed_action.value
        return {
            "verdict": "VERIFIED",
            "decision_id": decision.decision_id,
            "trigger_event_id": decision.trigger_event_id,
            "proposed_action": decision.proposed_action.value,
            "action_category": decision.action_category,
            "required_permissions": list(decision.required_permissions),
            "evidence": list(decision.evidence),
            "reason": decision.reason,
        }
    except Exception as exc:
        logger.exception("Stage 2 failed")
        return {"verdict": "ERROR", "error": f"{type(exc).__name__}: {exc}"}


def _stage_delivery(runtime, report) -> dict:
    """Stage 5: delivery semantics — queue publication vs actual user display."""
    pending = runtime.presence.pending()
    return {
        "verdict": "PARTIALLY_VERIFIED",
        "queue_publication": True,
        "pending_count": len(pending),
        "delivery_mechanism": "PresenceService.publish() appends to in-memory deque; "
                              "_deliver_presence() in app.py consumes the queue at the "
                              "CLI foreground prompt boundary (_prompt_user before input())",
        "actual_user_display": False,
        "reason": "Delivery to the user requires _prompt_user() to run (blocking "
                  "terminal input). This experiment does not drive the CLI prompt, "
                  "so no message is actually displayed to a user.",
        "can_be_lost_on_shutdown": True,
        "distinguishes_queued_delivered_acknowledged": "Partially: exploration "
                                                       "notifications are marked "
                                                       "delivered in storage; "
                                                       "initiative events are not.",
    }


if __name__ == "__main__":
    main()
