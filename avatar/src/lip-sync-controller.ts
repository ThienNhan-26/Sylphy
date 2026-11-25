/**
 * Amplitude-driven presentation adapter for Voice Runtime playback snapshots.
 *
 * This controller owns no Entity state and never starts/stops audio. The
 * playback position and session generation come from the read-only Voice API;
 * the controller only writes the VRM mouth/viseme expression channel.
 */
export interface VoicePlaybackSnapshot {
  enabled: boolean;
  state: string;
  is_speaking: boolean;
  active_session_id: string | null;
  entity_instance_id: string | null;
  session_generation: number;
  sequence: number;
  position_seconds: number;
  duration_seconds: number;
  amplitude: number;
}

export interface LipSyncOptions {
  noiseGate?: number;
  fullOpenAmplitude?: number;
  attackSeconds?: number;
  releaseSeconds?: number;
  maxSmoothingDeltaSeconds?: number;
}

const finiteNonNegative = (value: number): boolean => Number.isFinite(value) && value >= 0;

export class AmplitudeLipSyncController {
  private readonly noiseGate: number;
  private readonly fullOpenAmplitude: number;
  private readonly attackSeconds: number;
  private readonly releaseSeconds: number;
  private readonly maxSmoothingDeltaSeconds: number;
  private entityInstanceId: string | null = null;
  private generation = -1;
  private sessionId: string | null = null;
  private lastSequence = -1;
  private lastPosition = 0;
  private mouthWeight = 0;

  constructor(
    private readonly setMouthOpen: (weight: number) => void,
    options: LipSyncOptions = {},
  ) {
    this.noiseGate = options.noiseGate ?? 0.018;
    this.fullOpenAmplitude = options.fullOpenAmplitude ?? 0.24;
    this.attackSeconds = options.attackSeconds ?? 0.045;
    this.releaseSeconds = options.releaseSeconds ?? 0.11;
    this.maxSmoothingDeltaSeconds = options.maxSmoothingDeltaSeconds ?? 0.12;

    if (!Number.isFinite(this.noiseGate) || this.noiseGate < 0 || this.noiseGate >= 1) {
      throw new Error('noiseGate must be in [0, 1)');
    }
    if (!Number.isFinite(this.fullOpenAmplitude) || this.fullOpenAmplitude <= this.noiseGate || this.fullOpenAmplitude > 1) {
      throw new Error('fullOpenAmplitude must be greater than noiseGate and at most 1');
    }
    if (!finiteNonNegative(this.attackSeconds) || this.attackSeconds === 0) {
      throw new Error('attackSeconds must be positive');
    }
    if (!finiteNonNegative(this.releaseSeconds) || this.releaseSeconds === 0) {
      throw new Error('releaseSeconds must be positive');
    }
    if (!finiteNonNegative(this.maxSmoothingDeltaSeconds) || this.maxSmoothingDeltaSeconds === 0) {
      throw new Error('maxSmoothingDeltaSeconds must be positive');
    }
  }

  get currentWeight(): number {
    return this.mouthWeight;
  }

  /** Apply the newest Voice Runtime snapshot; stale sessions are ignored. */
  update(snapshot: VoicePlaybackSnapshot): boolean {
    if (!this.isValidSnapshot(snapshot)) {
      this.stop();
      return false;
    }

    if (snapshot.entity_instance_id !== this.entityInstanceId) {
      this.entityInstanceId = snapshot.entity_instance_id;
      this.generation = -1;
      this.sessionId = null;
      this.lastSequence = -1;
      this.lastPosition = 0;
      this.apply(0);
    }

    // Offline/disabled is a fail-closed signal, even if the offline projection
    // has a lower generation than the last connected session.
    if (!snapshot.enabled || ['offline', 'unavailable', 'disabled'].includes(snapshot.state)) {
      this.stop();
      return true;
    }
    if (snapshot.session_generation < this.generation) return false;

    if (snapshot.session_generation > this.generation) {
      this.generation = snapshot.session_generation;
      this.sessionId = snapshot.active_session_id;
      this.lastSequence = -1;
      this.lastPosition = 0;
      this.apply(0);
    } else if (this.sessionId !== snapshot.active_session_id) {
      // A session ID can change only with a generation bump. Reject ambiguous
      // same-generation payloads instead of allowing an old stream to animate.
      return false;
    }

    if (snapshot.sequence <= this.lastSequence) return false;
    if (snapshot.sequence > this.lastSequence) this.lastSequence = snapshot.sequence;

    const isActualPlayback = snapshot.enabled === true
      && snapshot.state === 'playing'
      && snapshot.is_speaking === true
      && typeof snapshot.active_session_id === 'string'
      && snapshot.active_session_id.length > 0;

    if (!isActualPlayback) {
      this.lastPosition = Math.max(this.lastPosition, snapshot.position_seconds);
      this.apply(0);
      return true;
    }

    if (snapshot.position_seconds + 1e-6 < this.lastPosition) return false;
    const delta = snapshot.position_seconds - this.lastPosition;
    if (delta <= 0) return true;

    this.lastPosition = snapshot.position_seconds;
    const rawAmplitude = Math.max(0, Math.min(snapshot.amplitude, 1));
    const target = Math.max(0, Math.min(
      (rawAmplitude - this.noiseGate) / (this.fullOpenAmplitude - this.noiseGate),
      1,
    ));
    const dt = Math.min(delta, this.maxSmoothingDeltaSeconds);
    const tau = target > this.mouthWeight ? this.attackSeconds : this.releaseSeconds;
    const alpha = 1 - Math.exp(-dt / tau);
    this.apply(this.mouthWeight + (target - this.mouthWeight) * alpha);
    return true;
  }

  /** Close only the mouth channel; mood and blink expressions remain owned elsewhere. */
  stop(): void {
    this.apply(0);
  }

  private apply(weight: number): void {
    this.mouthWeight = Math.max(0, Math.min(weight, 1));
    this.setMouthOpen(this.mouthWeight);
  }

  private isValidSnapshot(value: VoicePlaybackSnapshot): boolean {
    return typeof value === 'object' && value !== null
      && typeof value.enabled === 'boolean'
      && typeof value.state === 'string'
      && typeof value.is_speaking === 'boolean'
      && (value.active_session_id === null || typeof value.active_session_id === 'string')
      && (value.entity_instance_id === null || typeof value.entity_instance_id === 'string')
      && Number.isSafeInteger(value.session_generation) && value.session_generation >= 0
      && Number.isSafeInteger(value.sequence) && value.sequence >= 0
      && finiteNonNegative(value.position_seconds)
      && finiteNonNegative(value.duration_seconds)
      && Number.isFinite(value.amplitude) && value.amplitude >= 0 && value.amplitude <= 1;
  }
}
