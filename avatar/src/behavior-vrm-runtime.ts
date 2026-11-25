import type { HumanoidRig } from './humanoid-rig';
import { IdleMotionController } from './idle-motion';
import { HeadGazeController } from './head-gaze-controller';
import { AnimationClipRegistry } from './animation-clip-registry';
import type { VrmExpressionController } from './expression-controller';
import { AutomaticBlinkController } from './automatic-blink-controller';
import { BodyPoseController } from './body-pose-controller';

export type AvatarBehaviorName = 'idle' | 'processing' | 'reacting' | 'speaking' | 'interrupted' | 'paused' | 'shutdown';
export interface PresentationCommandLike {
  action: 'start' | 'stop'; command_id: string; owner: string; behavior: AvatarBehaviorName;
  clip: string; region: 'body' | 'head' | 'eyes' | 'face' | 'mouth'; priority: number;
  loop?: boolean; fade_in_seconds?: number; fade_out_seconds?: number; weight?: number;
  session_id?: string | null; sequence?: number;
}
export interface PresentationFrameLike { behavior: AvatarBehaviorName; commands: PresentationCommandLike[]; }

/** Renderer-side projection of the Entity's already-arbitrated presentation frame. */
export class BehaviorVrmRuntime {
  private behavior: AvatarBehaviorName | null = null;
  private readonly idle: IdleMotionController;
  private readonly gaze: HeadGazeController;
  private readonly blink: AutomaticBlinkController;
  private readonly body: BodyPoseController;
  private readonly commandClips = new Map<string, { clip: string; region: string }>();
  private disposed = false;
  private idleYielded = false;
  private bodyYielded = false;
  private gazeYielded = false;
  private idleIntensity = 1;
  private gazeTarget = { yaw: 0, pitch: 0 };

  constructor(
    rig: HumanoidRig,
    private readonly clips: AnimationClipRegistry,
    private readonly expressions: VrmExpressionController | null,
  ) {
    this.body = new BodyPoseController(rig);
    this.idle = new IdleMotionController(rig);
    this.gaze = new HeadGazeController(rig);
    this.blink = new AutomaticBlinkController(expressions);
    this.applyBehavior('idle');
    this.blink.setEnabled(true);
  }

  get currentBehavior(): AvatarBehaviorName | null { return this.behavior; }

  applyBehavior(behavior: AvatarBehaviorName): void {
    if (this.disposed) return;
    if (!['idle', 'processing', 'reacting', 'speaking', 'interrupted', 'paused', 'shutdown'].includes(behavior)) {
      throw new Error(`Unsupported avatar behavior: ${behavior}`);
    }
    if (this.behavior === behavior) return;
    this.behavior = behavior;
    switch (behavior) {
      case 'idle':
        this.body.setGesture('idle');
        this.idle.setEnabled(true); this.idleIntensity = 1; this.idle.setIntensity(1);
        this.gaze.setEnabled(true); this.gazeTarget = { yaw: 0, pitch: 0 }; this.gaze.setTarget(0, 0);
        this.expressions?.setMood(null);
        this.blink.setEnabled(true);
        break;
      case 'processing':
        this.body.setGesture('processing');
        this.idle.setEnabled(true); this.idleIntensity = 0.35; this.idle.setIntensity(0.35);
        this.gaze.setEnabled(true); this.gazeTarget = { yaw: 0.015, pitch: -0.09 }; this.gaze.setTarget(0.015, -0.09);
        this.expressions?.setMood('relaxed');
        this.blink.setEnabled(true);
        break;
      case 'reacting':
        this.body.setGesture('reacting');
        this.expressions?.setMood(null);
        this.idle.setEnabled(true); this.idleIntensity = 0.45; this.idle.setIntensity(0.45);
        this.gaze.setEnabled(true); this.gazeTarget = { yaw: 0.12, pitch: 0.025 }; this.gaze.setTarget(0.12, 0.025);
        this.blink.setEnabled(true);
        break;
      case 'speaking':
        this.body.setGesture('speaking');
        this.expressions?.setMood(null);
        this.idle.setEnabled(true); this.idleIntensity = 0.3; this.idle.setIntensity(0.3);
        this.gaze.setEnabled(true); this.gazeTarget = { yaw: 0, pitch: 0 }; this.gaze.setTarget(0, 0);
        this.blink.setEnabled(true);
        break;
      case 'interrupted':
        this.body.setGesture('interrupted');
        this.expressions?.setMood(null);
        this.idle.setEnabled(true); this.idleIntensity = 0.2; this.idle.setIntensity(0.2);
        this.gaze.setEnabled(true); this.gazeTarget = { yaw: 0, pitch: 0 }; this.gaze.setTarget(0, 0);
        this.expressions?.setViseme(null, 0);
        this.blink.setEnabled(true);
        break;
      case 'paused':
      case 'shutdown':
        this.body.setGesture('paused');
        this.body.setEnabled(true);
        this.bodyYielded = false;
        this.idle.setEnabled(false); this.gaze.setEnabled(false);
        this.clips.stopAll(0.12);
        this.expressions?.setMood(null);
        this.expressions?.setViseme(null, 0);
        this.blink.setEnabled(false);
        break;
    }
    if (behavior === 'idle' || behavior === 'processing' || behavior === 'reacting' || behavior === 'speaking' || behavior === 'interrupted') {
      const semanticClip = `behavior.${behavior}`;
      if (this.clips.has(semanticClip)) this.clips.play(semanticClip, {
        loop: behavior !== 'reacting' && behavior !== 'interrupted', channel: 'body',
      });
    }
  }

  /** Apply Python PresentationFrame JSON. Entity/Arbiter remains the authority. */
  applyPresentationFrame(frame: PresentationFrameLike): void {
    if (!frame || !Array.isArray(frame.commands)) throw new Error('Invalid presentation frame');
    this.applyBehavior(frame.behavior);
    if (frame.behavior === 'paused' || frame.behavior === 'shutdown') {
      this.clips.stopAll(0.12);
      this.commandClips.clear();
      return;
    }
    const desired = new Set<string>();
    for (const command of frame.commands) {
      if (!command || !command.command_id || !command.clip || !['start', 'stop'].includes(command.action)) continue;
      if (command.action === 'stop') {
        const old = this.commandClips.get(command.command_id);
        if (old) this.clips.stop(old.clip, command.fade_out_seconds ?? 0.15, old.region);
        this.commandClips.delete(command.command_id);
        continue;
      }
      desired.add(command.command_id);
      this.commandClips.set(command.command_id, { clip: command.clip, region: command.region });
      this.applySemanticCommand(command);
      if (this.clips.has(command.clip)) {
        this.clips.play(command.clip, {
          loop: command.loop ?? true,
          fadeInSeconds: command.fade_in_seconds ?? 0.18,
          weight: command.weight ?? 1,
          channel: command.region,
        });
      }
      // Semantic procedural clips are intentionally handled by applyBehavior;
      // mouth visemes are exclusively owned by the later playback-clock lip-sync phase.
    }
    for (const [commandId, active] of [...this.commandClips]) {
      if (!desired.has(commandId) && frame.commands.every((item) => item.command_id !== commandId)) {
        this.clips.stop(active.clip, 0.15, active.region);
        this.commandClips.delete(commandId);
      }
    }
  }

  setGaze(yaw: number, pitch: number): void { this.gazeTarget = { yaw, pitch }; this.gaze.setTarget(yaw, pitch); }

  private applySemanticCommand(command: PresentationCommandLike): void {
    switch (command.clip) {
      case 'idle.breathing': this.idle.setEnabled(true); this.idleIntensity = 1; this.idle.setIntensity(1); break;
      case 'idle.head_micro_motion': this.gaze.setEnabled(true); this.setGaze(0, 0); break;
      case 'idle.blink': this.blink.setEnabled(true); break;
      case 'thinking.posture': this.idle.setEnabled(true); this.idleIntensity = 0.35; this.idle.setIntensity(0.35); break;
      case 'thinking.head_pose':
      case 'thinking.gaze': this.gaze.setEnabled(true); this.setGaze(0.015, -0.09); break;
      case 'reacting.head_turn':
      case 'reacting.gaze_shift': this.gaze.setEnabled(true); this.setGaze(0.12, 0.025); break;
      case 'speaking.head_micro_motion': this.gaze.setEnabled(true); this.setGaze(0, 0); break;
      case 'interrupted.head_recoil': this.gaze.setEnabled(true); this.setGaze(-0.08, 0.04); break;
      case 'mouth.closed': this.expressions?.setViseme(null, 0); break;
      // Speaking lip-sync is intentionally deferred to the playback-clock phase.
      case 'speaking.lipsync': break;
      default: break;
    }
  }

  update(deltaSeconds: number): void {
    if (this.disposed) return;
    const active = this.behavior !== 'paused' && this.behavior !== 'shutdown';
    // Animation clips own their regions while active; procedural controllers yield and resume cleanly.
    if (active) {
      const bodyOwned = this.clips.activeChannels.has('body');
      if (bodyOwned !== this.bodyYielded) {
        this.bodyYielded = bodyOwned;
        this.body.setEnabled(!bodyOwned);
      }
      if (bodyOwned !== this.idleYielded) {
        this.idleYielded = bodyOwned;
        this.idle.setEnabled(!bodyOwned);
        if (!bodyOwned) this.idle.setIntensity(this.idleIntensity);
      }
      const headOwned = this.clips.activeChannels.has('head') || this.clips.activeChannels.has('eyes');
      if (headOwned !== this.gazeYielded) {
        this.gazeYielded = headOwned;
        this.gaze.setEnabled(!headOwned);
        if (!headOwned) this.gaze.setTarget(this.gazeTarget.yaw, this.gazeTarget.pitch);
      }
    }
    // Update torso first so arm aiming uses this frame's parent transforms.
    this.idle.update(deltaSeconds);
    this.body.update(deltaSeconds);
    this.gaze.update(deltaSeconds);
    this.blink.update(deltaSeconds);
    this.clips.update(deltaSeconds);
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    this.body.reset(); this.idle.reset(); this.gaze.reset(); this.clips.dispose(); this.commandClips.clear();
  }
}
