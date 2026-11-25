import * as THREE from 'three';

export interface ClipPlayOptions {
  loop?: boolean;
  repetitions?: number;
  fadeInSeconds?: number;
  weight?: number;
  timeScale?: number;
}

/** Owns Three.js AnimationMixer actions, semantic aliases, crossfades and interruption. */
export class AnimationClipRegistry {
  private readonly mixer: THREE.AnimationMixer;
  private readonly clips = new Map<string, THREE.AnimationClip>();
  private readonly aliases = new Map<string, string>();
  private readonly actions = new Map<string, THREE.AnimationAction>();
  private readonly activeByChannel = new Map<string, string>();

  constructor(root: THREE.Object3D, clips: readonly THREE.AnimationClip[] = []) {
    this.mixer = new THREE.AnimationMixer(root);
    this.mixer.addEventListener('finished', (event) => {
      const finishedName = [...this.actions.entries()].find(([, action]) => action === event.action)?.[0];
      if (!finishedName) return;
      for (const [channel, activeName] of this.activeByChannel) {
        if (activeName === finishedName) this.activeByChannel.delete(channel);
      }
    });
    clips.forEach((clip, index) => this.register(clip.name.trim() || `clip-${index + 1}`, clip));
  }

  get names(): string[] { return [...this.clips.keys()].sort(); }
  get current(): string | null { return this.activeByChannel.get('default') ?? null; }
  get activeChannels(): ReadonlyMap<string, string> { return this.activeByChannel; }
  has(name: string): boolean { return this.resolve(name) !== null; }

  register(name: string, clip: THREE.AnimationClip): void {
    const key = name.trim();
    if (!key) throw new Error('Animation clip name must not be blank');
    if (this.clips.has(key)) throw new Error(`Animation clip already registered: ${key}`);
    this.clips.set(key, clip);
    this.actions.set(key, this.mixer.clipAction(clip));
  }

  alias(semanticName: string, clipName: string): void {
    if (!this.clips.has(clipName)) throw new Error(`Unknown animation clip: ${clipName}`);
    this.aliases.set(semanticName, clipName);
  }

  play(name: string, options: ClipPlayOptions & { channel?: string } = {}): boolean {
    const resolved = this.resolve(name);
    if (!resolved) return false;
    const next = this.actions.get(resolved)!;
    const channel = options.channel ?? 'default';
    const activeName = this.activeByChannel.get(channel) ?? null;
    if (activeName === resolved && next.isRunning()) return true;
    const previous = activeName ? this.actions.get(activeName) ?? null : null;
    const fade = Math.max(0, options.fadeInSeconds ?? 0.2);
    next.reset();
    next.enabled = true;
    next.setEffectiveTimeScale(options.timeScale ?? 1);
    next.setEffectiveWeight(0);
    next.setLoop(options.loop === false ? THREE.LoopOnce : THREE.LoopRepeat,
      options.loop === false ? 1 : (options.repetitions ?? Infinity));
    if (options.loop === false) next.clampWhenFinished = true;
    next.play();
    if (previous && previous !== next) next.crossFadeFrom(previous, fade, true);
    else next.fadeIn(fade);
    next.setEffectiveWeight(THREE.MathUtils.clamp(options.weight ?? 1, 0, 1));
    this.activeByChannel.set(channel, resolved);
    return true;
  }

  stop(name?: string, fadeOutSeconds = 0.15, channel = 'default'): void {
    const target = name ? this.resolve(name) : (this.activeByChannel.get(channel) ?? null);
    if (!target) return;
    const action = this.actions.get(target);
    if (!action) return;
    action.fadeOut(Math.max(0, fadeOutSeconds));
    if (this.activeByChannel.get(channel) === target) this.activeByChannel.delete(channel);
  }

  stopAll(fadeOutSeconds = 0.1): void {
    for (const action of this.actions.values()) action.fadeOut(Math.max(0, fadeOutSeconds));
    this.activeByChannel.clear();
  }

  update(deltaSeconds: number): void { this.mixer.update(THREE.MathUtils.clamp(deltaSeconds, 0, 0.05)); }

  dispose(): void {
    this.stopAll(0);
    this.mixer.stopAllAction();
    this.mixer.uncacheRoot(this.mixer.getRoot());
    this.clips.clear(); this.aliases.clear(); this.actions.clear(); this.activeByChannel.clear();
  }

  private resolve(name: string): string | null {
    const resolved = this.aliases.get(name) ?? name;
    return this.clips.has(resolved) ? resolved : null;
  }
}
