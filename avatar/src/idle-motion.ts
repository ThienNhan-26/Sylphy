import * as THREE from 'three';
import type { HumanoidRig } from './humanoid-rig';

export interface IdleMotionOptions {
  breathingHz: number;
  breathingRotation: number;
  swayRotation: number;
  headMicroRotation: number;
  smoothing: number;
}

const DEFAULTS: IdleMotionOptions = {
  breathingHz: 0.19,
  breathingRotation: 0.024,
  swayRotation: 0.012,
  headMicroRotation: 0.012,
  smoothing: 4.5,
};

/** Procedural, low-amplitude idle motion. All offsets are relative to captured bind pose. */
export class IdleMotionController {
  private readonly options: IdleMotionOptions;
  private readonly base = new Map<string, THREE.Quaternion>();
  private enabled = true;
  private intensity = 1;
  private phase = 0;
  private blend = 1;
  private readonly scratch = new THREE.Quaternion();

  constructor(private readonly rig: HumanoidRig, options: Partial<IdleMotionOptions> = {}) {
    this.options = { ...DEFAULTS, ...options };
    for (const name of ['hips', 'spine', 'chest', 'upperChest', 'neck', 'head'] as const) {
      const node = rig.getBone(name);
      if (node) this.base.set(name, node.quaternion.clone());
    }
  }

  setEnabled(enabled: boolean): void { this.enabled = enabled; }
  setIntensity(value: number): void { this.intensity = THREE.MathUtils.clamp(value, 0, 1); }

  update(deltaSeconds: number): void {
    const dt = THREE.MathUtils.clamp(deltaSeconds, 0, 0.05);
    const target = this.enabled ? 1 : 0;
    this.blend = THREE.MathUtils.damp(this.blend, target, this.options.smoothing, dt);
    this.phase += dt * Math.PI * 2 * this.options.breathingHz;
    const breath = Math.sin(this.phase);
    const sway = Math.sin(this.phase * 0.47 + 0.8);
    const amount = this.intensity * this.blend;
    this.apply('spine', breath * this.options.breathingRotation * amount, 0, 0);
    this.apply('chest', breath * this.options.breathingRotation * 0.65 * amount, 0, 0);
    this.apply('hips', 0, 0, sway * this.options.swayRotation * amount);
    // Head/neck are exclusively driven by HeadGazeController to avoid transform ownership conflicts.
  }

  reset(): void {
    for (const [name, quaternion] of this.base) {
      const node = this.rig.getBone(name as 'hips' | 'spine' | 'chest' | 'upperChest' | 'neck' | 'head');
      if (node) node.quaternion.copy(quaternion);
    }
  }

  private apply(name: 'hips' | 'spine' | 'chest' | 'head', x: number, y: number, z: number): void {
    const node = this.rig.getBone(name);
    const base = this.base.get(name);
    if (!node || !base) return;
    this.scratch.setFromEuler(new THREE.Euler(x, y, z, 'XYZ'));
    node.quaternion.copy(base).multiply(this.scratch);
  }
}
