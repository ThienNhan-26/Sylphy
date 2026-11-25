"""Deterministic, stateful decision core. This module performs no I/O."""

from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import math
from pathlib import PurePosixPath, PureWindowsPath

from sylphy_entity.contracts.decision import (
    Behaviour,
    BrainState,
    CognitionFeedback,
    CognitionReceipt,
    CognitionRequestProposal,
    DecisionFrame,
    DriveInputs,
    Goal,
    GoalFeedback,
    GoalOutcome,
    GoalProposal,
    GoalStatus,
    IntentProposal,
)
from sylphy_entity.contracts.world import FactState, WorldFact, WorldSnapshot
from .drives import update_drives
from .novelty import novelty_score, record_visit
from .transitions import choose_behaviour

KNOWN_FILE_KINDS = {
    "filesystem.file_discovered",
    "filesystem.file_created",
    "filesystem.file_modified",
    "filesystem.file_restored",
    "filesystem.file_renamed",
}
_ACTIVE_STATUSES = {GoalStatus.ACTIVE, GoalStatus.WAITING}
_FAILURE_COOLDOWN_BASE = 30.0
_FAILURE_COOLDOWN_MAX = 3600.0
_MAX_GOALS = 512

def _file_revision_key(fact: WorldFact, payload: dict) -> str:
    """Novelty is per observed file revision, not merely per path.

    Identical duplicate watcher notifications map to the same key; a changed
    size/mtime maps to a new revision so an edited file can be re-evaluated.
    """
    material = "|".join((
        fact.subject,
        str(payload.get("relative_path", "")),
        str(payload.get("size_bytes", "")),
        str(payload.get("modified_ns", "")),
        str(payload.get("is_directory", False)),
    ))
    return f"{fact.subject}@{sha256(material.encode('utf-8')).hexdigest()[:20]}"

def inspection_intent_id(goal_id: str, attempt_count: int) -> str:
    digest = sha256((goal_id + "|inspect-file-metadata|" + str(attempt_count)).encode()).hexdigest()[:16]
    return f"intent-{digest}"



def _failure_cooldown(failure_count: int) -> float:
    return min(
        _FAILURE_COOLDOWN_BASE * (2 ** min(max(0, failure_count - 1), 10)),
        _FAILURE_COOLDOWN_MAX,
    )


class EntityBrain:
    """Pure Brain.step: immutable inputs in, a new state and proposals out.

    The Brain never checks whether an action is permitted and never executes
    proposals. The Runtime owns those responsibilities in Stage 4.
    """

    def step(
        self,
        dt: float,
        world: WorldSnapshot,
        state: BrainState,
        *,
        external_takeover: bool = False,
        recovering: bool = False,
        drive_inputs: DriveInputs | None = None,
        feedback: tuple[GoalFeedback, ...] = (),
        cognition_feedback: tuple[CognitionFeedback, ...] = (),
        cognition_available: bool = False,
        allow_cognition_requests: bool = True,
        allow_goal_proposals: bool = True,
    ) -> DecisionFrame:
        if not math.isfinite(float(dt)):
            raise ValueError("dt must be finite")
        dt = max(0.0, min(float(dt), 300.0))
        takeover = (
            external_takeover
            or world.user_interaction_active
            or bool(world.externally_owned_resources)
        )
        now = state.virtual_time + dt
        rationale: list[str] = []
        goals = list(state.goals)
        changed_goal_ids: set[str] = set()
        valid_feedback = False
        cognition_receipts = list(state.cognition_receipts)
        existing_cognition_ids = {item.request_id for item in cognition_receipts}

        # Apply evidence-backed outcome feedback before choosing the next state.
        for item in feedback:
            index = next((i for i, goal in enumerate(goals) if goal.goal_id == item.goal_id), None)
            if index is None:
                rationale.append(f"ignored feedback for unknown goal: {item.goal_id}")
                continue
            goal = goals[index]
            if goal.status in {GoalStatus.COMPLETED, GoalStatus.ABANDONED}:
                rationale.append(f"ignored terminal feedback for goal: {item.goal_id}")
                continue
            if item.outcome is GoalOutcome.SUCCEEDED:
                updated = replace(
                    goal,
                    status=GoalStatus.COMPLETED,
                    progress=1.0,
                    updated_at_virtual=now,
                    completion_evidence=goal.completion_evidence + (str(item.evidence_id),),
                    suspended_at_virtual=None,
                )
                rationale.append(f"goal completed with evidence: {item.goal_id}")
            elif item.outcome is GoalOutcome.FAILED:
                failures = goal.failure_count + 1
                cooldown = _failure_cooldown(failures)
                updated = replace(
                    goal,
                    status=GoalStatus.FAILED,
                    failure_count=failures,
                    cooldown_until_virtual=now + cooldown,
                    updated_at_virtual=now,
                    suspended_at_virtual=None,
                )
                rationale.append(
                    f"goal failed ({failures}); retry cooldown until {now + cooldown:.1f}: {item.goal_id}"
                )
            elif item.outcome is GoalOutcome.WAITING:
                updated = replace(
                    goal,
                    status=GoalStatus.WAITING,
                    progress=(item.progress if item.progress is not None else goal.progress),
                    updated_at_virtual=now,
                )
                rationale.append(f"goal is waiting: {item.goal_id}")
            else:
                updated = replace(
                    goal,
                    status=GoalStatus.ABANDONED,
                    updated_at_virtual=now,
                    suspended_at_virtual=None,
                )
                rationale.append(f"goal abandoned: {item.goal_id}")
            goals[index] = updated
            changed_goal_ids.add(updated.goal_id)
            valid_feedback = True

        # Model output is never an Intent. The Brain independently checks that
        # every cited source is still a known World fact before retaining it.
        currently_known_refs = {
            fact.last_observation_id
            for fact in world.facts
            if fact.state is FactState.KNOWN
        }
        for item in cognition_feedback:
            if item.request_id in existing_cognition_ids:
                continue
            cited_refs = set(item.summary_evidence_refs)
            for _, _, _, refs in item.findings:
                cited_refs.update(refs)
            for _, _, _, refs in item.proposals:
                cited_refs.update(refs)
            sources_are_current = bool(item.source_refs) and set(item.source_refs).issubset(currently_known_refs)
            citations_are_valid = cited_refs.issubset(set(item.source_refs))
            completed = item.status == "completed"
            accepted = completed and sources_are_current and citations_are_valid and bool(cited_refs)
            if not completed:
                review_reason = f"provider outcome: {item.status}"
            elif not citations_are_valid:
                review_reason = "rejected: output cited a source outside the request"
            elif not sources_are_current:
                review_reason = "rejected: one or more source observations are no longer current"
            elif not cited_refs:
                review_reason = "completed without evidence-backed content"
            else:
                review_reason = "accepted as evidence-bounded context; not an execution instruction"
            receipt = CognitionReceipt(
                request_id=item.request_id,
                status=item.status,
                accepted_as_evidence=accepted,
                review_reason=review_reason,
                source_refs=item.source_refs,
                summary=item.summary[:4000] if accepted else "",
                findings=(
                    tuple(
                        f"{kind}: {statement} (confidence={confidence:.3f})"
                        for kind, statement, confidence, _ in item.findings
                    )[:16]
                    if accepted else ()
                ),
                proposals=(
                    tuple(
                        f"{kind}: {subject} — {rationale}"
                        for kind, subject, rationale, _ in item.proposals
                    )[:8]
                    if accepted else ()
                ),
                model=item.model,
                elapsed_ms=max(0, item.elapsed_ms),
                error_code=item.error_code,
                processed_at_virtual=now,
            )
            cognition_receipts.append(receipt)
            existing_cognition_ids.add(item.request_id)
            rationale.append(f"cognition result reviewed: {item.request_id}; {review_reason}")

        was_yielded = state.yielded
        yielded_since = state.yielded_since_virtual
        resumed = was_yielded and not takeover

        if takeover:
            if not was_yielded:
                yielded_since = now
            for index, goal in enumerate(goals):
                if goal.status in _ACTIVE_STATUSES:
                    suspended = replace(
                        goal,
                        status=GoalStatus.SUSPENDED,
                        resume_status=goal.status,
                        suspended_at_virtual=now,
                        updated_at_virtual=now,
                    )
                    goals[index] = suspended
                    changed_goal_ids.add(suspended.goal_id)
            rationale.append("user interaction or externally owned resource requires yielding")
        elif resumed:
            pause_duration = max(0.0, now - (yielded_since if yielded_since is not None else now))
            for index, goal in enumerate(goals):
                if goal.status is GoalStatus.SUSPENDED:
                    deadline = goal.deadline_at_virtual
                    if deadline is not None:
                        deadline += pause_duration
                    restored = replace(
                        goal,
                        status=goal.resume_status or GoalStatus.ACTIVE,
                        deadline_at_virtual=deadline,
                        suspended_at_virtual=None,
                        resume_status=None,
                        updated_at_virtual=now,
                    )
                    goals[index] = restored
                    changed_goal_ids.add(restored.goal_id)
            # Transient behaviour latches are intentionally not restored.
            yielded_since = None
            rationale.append("resumed after external takeover; transient latches reset")

        # Expire active goals using virtual time. Suspended goals have their
        # deadline shifted when resumed, so time spent yielded is not charged.
        for index, goal in enumerate(goals):
            if (
                goal.status in _ACTIVE_STATUSES
                and goal.deadline_at_virtual is not None
                and now > goal.deadline_at_virtual
            ):
                expired = replace(
                    goal,
                    status=GoalStatus.FAILED,
                    failure_count=goal.failure_count + 1,
                    cooldown_until_virtual=now + _failure_cooldown(goal.failure_count + 1),
                    updated_at_virtual=now,
                )
                goals[index] = expired
                changed_goal_ids.add(expired.goal_id)
                rationale.append(f"goal deadline expired: {expired.goal_id}")

        facts = tuple(fact for fact in world.facts if fact.state is FactState.KNOWN)
        active_goals = tuple(goal for goal in goals if goal.status is GoalStatus.ACTIVE)
        waiting_goals = tuple(goal for goal in goals if goal.status is GoalStatus.WAITING)
        blocked_subjects = {
            goal.subject
            for goal in goals
            if goal.status in {GoalStatus.ACTIVE, GoalStatus.WAITING, GoalStatus.SUSPENDED}
        }

        # Build candidates first, then update drives so current explicit inputs
        # can influence this tick's policy decision, not only the next tick.
        raw_candidates: list[tuple[WorldFact, str, float, float, float, float, Goal | None, float, str]] = []
        for fact in facts:
            if not fact.subject.startswith("file:"):
                continue
            payload = dict(fact.value or {})
            kind = payload.get("kind")
            if kind not in KNOWN_FILE_KINDS:
                continue
            relative_path = self._safe_relative_path(payload.get("relative_path"))
            if relative_path is None:
                rationale.append(f"ignored unsafe or missing relative path: {fact.subject}")
                continue
            permission_scope = fact.permission_scope
            if not (permission_scope.startswith("sandbox:") or permission_scope == "simulated:sandbox"):
                rationale.append(f"ignored fact outside an allowed sandbox scope: {fact.subject}")
                continue
            if fact.subject in blocked_subjects:
                continue

            relevance = self._score_value(payload.get("relevance", 0.6), default=0.6)
            risk = self._score_value(payload.get("risk", 0.1), default=0.1)
            cost = self._score_value(payload.get("estimated_cost", 0.2), default=0.2)
            verification_factor = {
                "confirmed": 1.0,
                "inferred": 0.7,
                "unverified": 0.35,
            }.get(fact.verification, 0.35)
            confidence = fact.confidence if fact.confidence is not None else 0.5
            evidence_quality = max(0.0, min(1.0, confidence * verification_factor))
            revision_key = _file_revision_key(fact, payload)
            score = novelty_score(
                revision_key,
                state.novelty,
                now,
                relevance=relevance * evidence_quality,
            )
            if score <= 0.15:
                continue
            previous = next(
                (goal for goal in reversed(goals)
                 if goal.kind == "observe_file_change" and goal.subject == fact.subject),
                None,
            )
            if previous is not None:
                if previous.status is GoalStatus.FAILED and now < previous.cooldown_until_virtual:
                    continue
                if previous.status is GoalStatus.ABANDONED:
                    continue
                if previous.status is GoalStatus.UNKNOWN and previous.source == fact.last_observation_id:
                    # An UNKNOWN execution is not retried against the same source
                    # observation. Runtime also quarantines its file revision across
                    # restart; a later, different observation is eligible as new work.
                    continue
            urgency = self._score_value(payload.get("urgency", 0.25), default=0.25)
            deadline_seconds = self._duration_value(payload.get("deadline_seconds", 0.0), default=0.0)
            raw_candidates.append((fact, relative_path, revision_key, score, relevance, risk, cost, previous, deadline_seconds, permission_scope))

        explicit_inputs = drive_inputs or DriveInputs()
        novelty_pressure = max((item[3] for item in raw_candidates), default=0.0)
        drives = update_drives(
            state.drives,
            dt=dt,
            inputs=DriveInputs(
                curiosity=max(explicit_inputs.curiosity, novelty_pressure),
                continuity=max(
                    explicit_inputs.continuity,
                    0.5 if (active_goals or world.running_work or world.active_goal_ids) else 0.0,
                ),
                social_responsiveness=max(
                    explicit_inputs.social_responsiveness,
                    0.7 if takeover else 0.0,
                ),
                self_maintenance=explicit_inputs.self_maintenance,
                resource_pressure=max(explicit_inputs.resource_pressure, world.resource_pressure),
            ),
        )

        candidates: list[GoalProposal] = []
        for fact, relative_path, revision_key, score, relevance, risk, cost, previous, deadline_seconds, permission_scope in raw_candidates:
            payload = dict(fact.value or {})
            urgency = self._score_value(payload.get("urgency", 0.25), default=0.25)
            drive_relevance = drives.curiosity * score + drives.continuity * (0.25 if active_goals else 0.0)
            repeated_failure_penalty = min(0.20, (previous.failure_count * 0.05) if previous else 0.0)
            resource_penalty = 0.08 * drives.resource_conservation * cost
            priority = max(
                0.0,
                min(
                    1.0,
                    0.30 * score
                    + 0.20 * relevance
                    + 0.20 * urgency
                    + 0.20 * drive_relevance
                    - 0.06 * cost
                    - 0.12 * risk
                    - repeated_failure_penalty
                    - resource_penalty,
                ),
            )
            candidates.append(
                GoalProposal(
                    kind="observe_file_change",
                    subject=fact.subject,
                    source=fact.last_observation_id,
                    target=relative_path,
                    priority=priority,
                    urgency=urgency,
                    desired_outcome="record current sandbox file metadata",
                    reason=(
                        f"known sandbox fact; novelty={score:.3f}; relevance={relevance:.3f}; "
                        f"risk={risk:.3f}; repeated_failure_penalty={repeated_failure_penalty:.3f}; "
                        f"resource_penalty={resource_penalty:.3f}"
                    ),
                    cost=cost,
                    risk=risk,
                    relevance=relevance,
                    required_permissions=("OBSERVE_SANDBOX",),
                    permission_scope=permission_scope,
                    deadline_seconds=deadline_seconds if deadline_seconds > 0 else None,
                )
            )

        candidates.sort(key=lambda item: (-item.priority, item.subject))
        # One active pursuit at a time. Other known facts remain in the World
        # and can be considered after the current goal completes or waits.
        selected = (
            candidates[:1]
            if allow_goal_proposals and not takeover and not recovering and not active_goals
            else []
        )

        proposals: list[GoalProposal] = []
        intents: list[IntentProposal] = []
        history = state.novelty
        for proposal in selected:
            selected_fact = next(fact for fact in facts if fact.subject == proposal.subject)
            selected_revision_key = _file_revision_key(selected_fact, dict(selected_fact.value or {}))
            stable_id = sha256(f"{proposal.kind}|{proposal.subject}".encode()).hexdigest()[:16]
            goal_id = f"goal-{stable_id}"
            existing_index = next((i for i, goal in enumerate(goals) if goal.goal_id == goal_id), None)
            if existing_index is None:
                goal = Goal(
                    goal_id=goal_id,
                    kind=proposal.kind,
                    subject=proposal.subject,
                    source=proposal.source,
                    priority=proposal.priority,
                    urgency=proposal.urgency,
                    desired_outcome=proposal.desired_outcome,
                    target=proposal.target,
                    status=GoalStatus.ACTIVE,
                    created_at_virtual=now,
                    updated_at_virtual=now,
                    cost=proposal.cost,
                    risk=proposal.risk,
                    relevance=proposal.relevance,
                    required_permissions=proposal.required_permissions,
                    permission_scope=proposal.permission_scope,
                    deadline_at_virtual=(
                        now + min(proposal.deadline_seconds, 604800.0)
                        if proposal.deadline_seconds is not None and proposal.deadline_seconds > 0
                        else None
                    ),
                    attempt_count=1,
                )
                goals.append(goal)
                changed_goal_ids.add(goal_id)
                proposals.append(proposal)
                history = record_visit(history, selected_revision_key, now)
                rationale.append(f"selected goal: {proposal.subject}; priority={proposal.priority:.3f}")
            else:
                previous = goals[existing_index]
                if previous.status is GoalStatus.UNKNOWN and previous.source != proposal.source:
                    reopened = replace(
                        previous,
                        status=GoalStatus.ACTIVE,
                        source=proposal.source,
                        target=proposal.target,
                        priority=proposal.priority,
                        urgency=proposal.urgency,
                        required_permissions=proposal.required_permissions,
                        permission_scope=proposal.permission_scope,
                        updated_at_virtual=now,
                        cost=proposal.cost,
                        risk=proposal.risk,
                        relevance=proposal.relevance,
                        deadline_at_virtual=(
                            now + proposal.deadline_seconds
                            if proposal.deadline_seconds is not None and proposal.deadline_seconds > 0
                            else None
                        ),
                        cooldown_until_virtual=0.0,
                        progress=0.0,
                        completion_evidence=(),
                        suspended_at_virtual=None,
                        resume_status=None,
                        attempt_count=previous.attempt_count + 1,
                    )
                    goals[existing_index] = reopened
                    changed_goal_ids.add(goal_id)
                    proposals.append(proposal)
                    history = record_visit(history, selected_revision_key, now)
                    rationale.append(f"reopened UNKNOWN goal for a new file revision: {proposal.subject}")
                elif previous.status is GoalStatus.FAILED and now >= previous.cooldown_until_virtual:
                    reopened = replace(
                        previous,
                        status=GoalStatus.ACTIVE,
                        source=proposal.source,
                        target=proposal.target,
                        priority=proposal.priority,
                        urgency=proposal.urgency,
                        required_permissions=proposal.required_permissions,
                        permission_scope=proposal.permission_scope,
                        updated_at_virtual=now,
                        cost=proposal.cost,
                        risk=proposal.risk,
                        relevance=proposal.relevance,
                        deadline_at_virtual=(
                            now + proposal.deadline_seconds
                            if proposal.deadline_seconds is not None and proposal.deadline_seconds > 0
                            else None
                        ),
                        cooldown_until_virtual=0.0,
                        attempt_count=previous.attempt_count + 1,
                    )
                    goals[existing_index] = reopened
                    changed_goal_ids.add(goal_id)
                    proposals.append(proposal)
                    history = record_visit(history, selected_revision_key, now)
                    rationale.append(f"reopened goal after cooldown: {proposal.subject}")
                # A completed goal is reconsidered only after novelty recovers.
                elif previous.status is GoalStatus.COMPLETED:
                    reopened = replace(
                        previous,
                        status=GoalStatus.ACTIVE,
                        source=proposal.source,
                        target=proposal.target,
                        priority=proposal.priority,
                        urgency=proposal.urgency,
                        required_permissions=proposal.required_permissions,
                        permission_scope=proposal.permission_scope,
                        updated_at_virtual=now,
                        cost=proposal.cost,
                        risk=proposal.risk,
                        relevance=proposal.relevance,
                        deadline_at_virtual=(
                            now + proposal.deadline_seconds
                            if proposal.deadline_seconds is not None and proposal.deadline_seconds > 0
                            else None
                        ),
                        progress=0.0,
                        completion_evidence=(),
                        attempt_count=previous.attempt_count + 1,
                    )
                    goals[existing_index] = reopened
                    changed_goal_ids.add(goal_id)
                    proposals.append(proposal)
                    history = record_visit(history, selected_revision_key, now)
                    rationale.append(f"revisited goal after novelty recovery: {proposal.subject}")
                else:
                    continue

            current_goal = next(goal for goal in goals if goal.goal_id == goal_id)
            intent_id = inspection_intent_id(goal_id, current_goal.attempt_count)
            intents.append(
                IntentProposal(
                    intent_id=intent_id,
                    goal_id=goal_id,
                    capability="inspect_file_metadata",
                    target=proposal.target,
                    arguments=(("relative_path", proposal.target),),
                    reason=proposal.reason,
                    permission_class="OBSERVE_SANDBOX",
                    permission_scope=proposal.permission_scope,
                    required_resources=(f"filesystem:sandbox:{proposal.target}",),
                    expected_outcome=proposal.desired_outcome,
                    priority=proposal.priority,
                    source_observation_id=proposal.source,
                )
            )

        # Keep durable goal history bounded. Active/suspended/waiting goals are
        # retained preferentially; old terminal history is trimmed first.
        if len(goals) > _MAX_GOALS:
            terminal = [g for g in goals if g.status in {GoalStatus.COMPLETED, GoalStatus.FAILED, GoalStatus.ABANDONED}]
            retained = [g for g in goals if g.status not in {GoalStatus.COMPLETED, GoalStatus.FAILED, GoalStatus.ABANDONED}]
            terminal = terminal[-max(0, _MAX_GOALS - len(retained)):]
            goals = retained + terminal

        cognition_proposals: list[CognitionRequestProposal] = []
        requested_refs = list(state.cognition_requested_refs)
        requested_set = set(requested_refs)
        if cognition_available and allow_cognition_requests and not takeover and not recovering:
            # The first vertical slice asks for a bounded summary of filesystem
            # metadata only. It never reads file contents or infers a file's contents.
            for candidate in raw_candidates:
                fact = candidate[0]
                source_ref = fact.last_observation_id
                if source_ref in requested_set:
                    continue
                request_id = "cog-" + sha256(
                    f"summarize|{source_ref}".encode("utf-8")
                ).hexdigest()[:24]
                cognition_proposals.append(
                    CognitionRequestProposal(
                        request_id=request_id,
                        operation="summarize",
                        purpose=(
                            "Summarize this newly observed sandbox file metadata. "
                            "Do not infer file contents or recommend executable actions."
                        ),
                        source_refs=(source_ref,),
                    )
                )
                requested_refs.append(source_ref)
                requested_set.add(source_ref)
                rationale.append(f"bounded cognition requested for observation: {source_ref}")
                break

        behaviour = choose_behaviour(
            external_takeover=takeover,
            has_candidate=bool(proposals),
            active_goal=any(g.status is GoalStatus.ACTIVE for g in goals),
            waiting_goal=any(g.status is GoalStatus.WAITING for g in goals),
            world_changed=(world.version != state.last_world_version),
            recovering=recovering,
            feedback_received=valid_feedback,
        )
        transition = (state.behaviour, behaviour) if state.behaviour != behaviour else None

        if recovering:
            rationale.append("runtime requested recovery behaviour")
        if not rationale:
            if any(goal.status is GoalStatus.ACTIVE for goal in goals):
                rationale.append("continuing an active goal; no new action proposal this tick")
            elif any(goal.status is GoalStatus.WAITING for goal in goals):
                rationale.append("waiting for a goal precondition or external event")
            else:
                rationale.append("no eligible goal; Entity remains available and observes on world change")

        updated_goals = tuple(goals)
        goal_updates = tuple(goal for goal in updated_goals if goal.goal_id in changed_goal_ids)
        next_state = BrainState(
            behaviour=behaviour,
            virtual_time=now,
            drives=drives,
            goals=updated_goals,
            novelty=history,
            yielded=takeover,
            yielded_since_virtual=yielded_since if takeover else None,
            transient_latches=() if resumed or takeover else state.transient_latches,
            last_world_version=world.version,
            last_reason=rationale[-1],
            tick_count=state.tick_count + 1,
            cognition_requested_refs=tuple(requested_refs[-256:]),
            cognition_receipts=tuple(cognition_receipts[-32:]),
        )
        return DecisionFrame(
            next_state=next_state,
            behaviour=behaviour,
            transition=transition,
            goal_proposals=tuple(proposals),
            goal_updates=goal_updates,
            intent_proposals=tuple(intents),
            active_goal_ids=tuple(g.goal_id for g in updated_goals if g.status is GoalStatus.ACTIVE),
            rationale=tuple(rationale),
            should_yield=takeover,
            cognition_proposals=tuple(cognition_proposals),
        )

    @staticmethod
    def _safe_relative_path(value: object) -> str | None:
        if not isinstance(value, str) or not value.strip():
            return None
        normalized = value.replace("\\", "/")
        path = PurePosixPath(normalized)
        windows_path = PureWindowsPath(value)
        if (
            path.is_absolute()
            or windows_path.is_absolute()
            or bool(windows_path.drive)
            or ":" in normalized
            or any(part in {"", ".", ".."} for part in path.parts)
        ):
            return None
        return path.as_posix()

    @staticmethod
    def _duration_value(value: object, *, default: float) -> float:
        try:
            duration = float(value)
        except (TypeError, ValueError):
            return default
        if not math.isfinite(duration):
            return default
        return max(0.0, min(604800.0, duration))

    @staticmethod
    def _score_value(value: object, *, default: float) -> float:
        try:
            score = float(value)
        except (TypeError, ValueError):
            return default
        if not math.isfinite(score):
            return default
        return max(0.0, min(1.0, score))
