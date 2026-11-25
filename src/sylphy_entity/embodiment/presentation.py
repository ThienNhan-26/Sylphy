"""Behavior-to-animation mapping and renderer-neutral presentation runtime."""
from __future__ import annotations
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, Sequence
from .arbiter import ArbitrationSnapshot, DEFAULT_PRIORITIES
from .contracts import AvatarBehavior, BehaviorClaim, BodyRegion


class AnimationAction(StrEnum):
    START = "start"
    STOP = "stop"


@dataclass(frozen=True, slots=True)
class AnimationCommand:
    action: AnimationAction
    command_id: str
    owner: str
    behavior: AvatarBehavior
    clip: str
    region: BodyRegion
    priority: int
    loop: bool = True
    fade_in_seconds: float = 0.18
    fade_out_seconds: float = 0.15
    weight: float = 1.0
    session_id: str | None = None
    sequence: int = 0

    def __post_init__(self) -> None:
        if not self.command_id.strip() or not self.owner.strip() or not self.clip.strip():
            raise ValueError("command_id, owner and clip must not be blank")
        if self.priority < 0 or self.sequence < 0:
            raise ValueError("priority and sequence must be non-negative")
        if self.fade_in_seconds < 0 or self.fade_out_seconds < 0:
            raise ValueError("fade durations must be non-negative")
        if not 0.0 <= self.weight <= 1.0:
            raise ValueError("weight must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class PresentationFrame:
    behavior: AvatarBehavior
    commands: tuple[AnimationCommand, ...]


class BehaviorAnimationMapper:
    """Maps winning region claims to semantic clip IDs; renderer resolves actual assets."""
    CLIPS: dict[AvatarBehavior, dict[BodyRegion, str]] = {
        AvatarBehavior.IDLE: {
            BodyRegion.BODY: "idle.breathing",
            BodyRegion.HEAD: "idle.head_micro_motion",
            BodyRegion.EYES: "idle.blink",
        },
        AvatarBehavior.PROCESSING: {
            BodyRegion.BODY: "thinking.posture",
            BodyRegion.HEAD: "thinking.head_pose",
            BodyRegion.EYES: "thinking.gaze",
        },
        AvatarBehavior.REACTING: {
            BodyRegion.HEAD: "reacting.head_turn",
            BodyRegion.EYES: "reacting.gaze_shift",
            BodyRegion.FACE: "reacting.expression",
        },
        AvatarBehavior.SPEAKING: {
            BodyRegion.HEAD: "speaking.head_micro_motion",
            BodyRegion.FACE: "speaking.expression",
            BodyRegion.MOUTH: "speaking.lipsync",
        },
        AvatarBehavior.INTERRUPTED: {
            BodyRegion.HEAD: "interrupted.head_recoil",
            BodyRegion.FACE: "interrupted.expression",
            BodyRegion.MOUTH: "mouth.closed",
        },
        AvatarBehavior.PAUSED: {region: "paused.neutral" for region in BodyRegion},
        AvatarBehavior.SHUTDOWN: {region: "shutdown.neutral" for region in BodyRegion},
    }

    def map(self, snapshot: ArbitrationSnapshot) -> PresentationFrame:
        commands: list[AnimationCommand] = []
        if snapshot.behavior is AvatarBehavior.IDLE and not snapshot.region_owners:
            for region, clip in self.CLIPS[AvatarBehavior.IDLE].items():
                commands.append(AnimationCommand(
                    AnimationAction.START, f"system:idle:{region.value}", "system:idle",
                    AvatarBehavior.IDLE, clip, region, 10, loop=True,
                ))
            return PresentationFrame(AvatarBehavior.IDLE, tuple(commands))

        for region, claim in snapshot.region_owners.items():
            clip = self.CLIPS[claim.behavior].get(region)
            if clip is None:
                continue
            priority = claim.priority if claim.priority is not None else DEFAULT_PRIORITIES[claim.behavior]
            commands.append(AnimationCommand(
                action=AnimationAction.START,
                command_id=f"{claim.owner}:{region.value}",
                owner=claim.owner,
                behavior=claim.behavior,
                clip=clip,
                region=region,
                priority=priority,
                loop=claim.behavior not in {AvatarBehavior.REACTING, AvatarBehavior.INTERRUPTED},
                session_id=claim.session_id,
                sequence=claim.sequence,
            ))
        return PresentationFrame(snapshot.behavior, tuple(sorted(commands, key=lambda c: c.region.value)))


class AnimationRenderer(Protocol):
    async def apply(self, command: AnimationCommand) -> None: ...


class PresentationRuntime:
    """Applies diffed presentation commands; it owns no Entity or Avatar truth state."""
    def __init__(self, renderer: AnimationRenderer, mapper: BehaviorAnimationMapper | None = None):
        self.renderer = renderer
        self.mapper = mapper or BehaviorAnimationMapper()
        self._active: dict[str, AnimationCommand] = {}
        self._behavior = AvatarBehavior.IDLE

    @property
    def behavior(self) -> AvatarBehavior:
        return self._behavior

    @property
    def active_commands(self) -> tuple[AnimationCommand, ...]:
        return tuple(self._active[key] for key in sorted(self._active))

    async def render(self, snapshot: ArbitrationSnapshot) -> PresentationFrame:
        frame = self.mapper.map(snapshot)
        desired = {command.command_id: command for command in frame.commands}
        # Stop commands whose ownership/clip is no longer selected.
        for command_id, old in tuple(self._active.items()):
            new = desired.get(command_id)
            if new is None or new.clip != old.clip or new.owner != old.owner:
                await self.renderer.apply(AnimationCommand(
                    action=AnimationAction.STOP,
                    command_id=old.command_id,
                    owner=old.owner,
                    behavior=old.behavior,
                    clip=old.clip,
                    region=old.region,
                    priority=old.priority,
                    loop=False,
                    fade_out_seconds=old.fade_out_seconds,
                    weight=0.0,
                    session_id=old.session_id,
                    sequence=old.sequence,
                ))
                self._active.pop(command_id, None)
        # Start only new/changed commands; unchanged loops are left running.
        for command_id, command in desired.items():
            if command_id not in self._active:
                await self.renderer.apply(command)
                self._active[command_id] = command
        self._behavior = frame.behavior
        return frame

    async def stop_all(self) -> None:
        for command in tuple(self._active.values()):
            await self.renderer.apply(AnimationCommand(
                action=AnimationAction.STOP, command_id=command.command_id,
                owner=command.owner, behavior=command.behavior, clip=command.clip,
                region=command.region, priority=command.priority, loop=False,
                fade_out_seconds=command.fade_out_seconds, weight=0.0,
                session_id=command.session_id, sequence=command.sequence,
            ))
        self._active.clear()
        self._behavior = AvatarBehavior.IDLE
