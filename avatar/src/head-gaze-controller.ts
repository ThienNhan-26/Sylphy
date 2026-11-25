import * as THREE from 'three';
import type { HumanoidRig } from './humanoid-rig';

export interface GazeTarget { yaw: number; pitch: number; }

/** Model-local head/neck/eye pose controller with clamped targets and smooth return. */
export class HeadGazeController {
  private readonly base = new Map<string, THREE.Quaternion>();
  private target: GazeTarget = { yaw: 0, pitch: 0 };
  private current: GazeTarget = { yaw: 0, pitch: 0 };
  private enabled = true;
  private phase = 0;
  private readonly scratch = new THREE.Quaternion();

  constructor(private readonly rig: HumanoidRig, private readonly responsiveness = 5.5) {
    for (const name of ['neck', 'head', 'leftEye', 'rightEye'] as const) {
      const node = rig.getBone(name);
      if (node) this.base.set(name, node.quaternion.clone());
    }
  }

  setTarget(yaw: number, pitch: number): void {
    this.target = {
      yaw: THREE.MathUtils.clamp(yaw, -0.65, 0.65),
      pitch: THREE.MathUtils.clamp(pitch, -0.35, 0.35),
    };
  }

  setEnabled(enabled: boolean): void { this.enabled = enabled; if (!enabled) this.setTarget(0, 0); }

  update(deltaSeconds: number): void {
    const dt = THREE.MathUtils.clamp(deltaSeconds, 0, 0.05);
    this.phase += dt;
    const idleYaw = Math.sin(this.phase * 0.53) * 0.012;
    const idlePitch = Math.sin(this.phase * 0.37 + 1.1) * 0.007;
    const target = this.enabled
      ? { yaw: this.target.yaw + idleYaw, pitch: this.target.pitch + idlePitch }
      : { yaw: 0, pitch: 0 };
    this.current.yaw = THREE.MathUtils.damp(this.current.yaw, target.yaw, this.responsiveness, dt);
    this.current.pitch = THREE.MathUtils.damp(this.current.pitch, target.pitch, this.responsiveness, dt);
    this.apply('neck', this.current.yaw * 0.18, this.current.pitch * 0.18);
    this.apply('head', this.current.yaw * 0.52, this.current.pitch * 0.52);
    this.apply('leftEye', this.current.yaw * 0.3, this.current.pitch * 0.3);
    this.apply('rightEye', this.current.yaw * 0.3, this.current.pitch * 0.3);
  }

  reset(): void {
    for (const [name, quaternion] of this.base) {
      const node = this.rig.getBone(name as 'neck' | 'head' | 'leftEye' | 'rightEye');
      if (node) node.quaternion.copy(quaternion);
    }
    this.current = { yaw: 0, pitch: 0 };
    this.target = { yaw: 0, pitch: 0 };
  }

  private apply(name: 'neck' | 'head' | 'leftEye' | 'rightEye', yaw: number, pitch: number): void {
    const node = this.rig.getBone(name);
    const base = this.base.get(name);
    if (!node || !base) return;
    this.scratch.setFromEuler(new THREE.Euler(pitch, yaw, 0, 'YXZ'));
    node.quaternion.copy(base).multiply(this.scratch);
  }
}
