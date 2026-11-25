import * as THREE from 'three';
import type { HumanoidRig } from './humanoid-rig';

export type BodyGesture = 'idle' | 'speaking' | 'reacting' | 'processing' | 'interrupted' | 'paused';
type ArmSide = 'left' | 'right';

interface ArmBones {
  upper: THREE.Object3D;
  lower: THREE.Object3D;
  hand: THREE.Object3D;
  baseUpper: THREE.Quaternion;
  baseLower: THREE.Quaternion;
  sideSign: number;
}

interface ArmPoseProfile {
  upperOutward: number;
  upperForward: number;
  forearmOutward: number;
  forearmForward: number;
}

const PROFILES: Record<Exclude<BodyGesture, 'speaking'>, ArmPoseProfile> = {
  // Arms are kept visibly outside the torso to reduce the tucked-in silhouette
  // common with wide-sleeved VRM outfits. Values are direction ratios, not angles.
  idle: { upperOutward: 0.48, upperForward: 0, forearmOutward: 0.10, forearmForward: 0 },
  processing: { upperOutward: 0.28, upperForward: 0, forearmOutward: 0.04, forearmForward: 0 },
  reacting: { upperOutward: 0.92, upperForward: 0, forearmOutward: 0.20, forearmForward: 0 },
  interrupted: { upperOutward: 0.66, upperForward: 0, forearmOutward: 0.14, forearmForward: 0 },
  paused: { upperOutward: 0.48, upperForward: 0, forearmOutward: 0.10, forearmForward: 0 },
};

/**
 * Procedural relaxed-arm pose and lightweight behavior gestures for normalized VRM bones.
 * The controller aims both upper-arm and forearm segments in world space, so it does not
 * assume identical local bone axes across avatar models. Clothing collision is not solved;
 * the outward clearance is a pose heuristic that can be tuned per avatar.
 */
export class BodyPoseController {
  private readonly base = new Map<string, THREE.Quaternion>();
  private readonly arms = new Map<ArmSide, ArmBones>();
  private readonly targetDirection = new THREE.Vector3();
  private readonly startPosition = new THREE.Vector3();
  private readonly endPosition = new THREE.Vector3();
  private readonly currentDirection = new THREE.Vector3();
  private readonly deltaRotation = new THREE.Quaternion();
  private readonly currentWorld = new THREE.Quaternion();
  private readonly desiredWorld = new THREE.Quaternion();
  private readonly parentWorldInverse = new THREE.Quaternion();
  private readonly desiredLocal = new THREE.Quaternion();
  private readonly blendedLocal = new THREE.Quaternion();
  private readonly smoothing = 7.0;
  private enabled = true;
  private gesture: BodyGesture = 'idle';
  private phase = 0;
  private blend = 0;

  constructor(private readonly rig: HumanoidRig) {
    const names = [
      'hips', 'spine', 'chest',
      'leftShoulder', 'leftUpperArm', 'leftLowerArm', 'leftHand',
      'rightShoulder', 'rightUpperArm', 'rightLowerArm', 'rightHand',
    ] as const;
    for (const name of names) {
      const node = rig.getBone(name);
      if (node) this.base.set(name, node.quaternion.clone());
    }
    this.captureArm('left');
    this.captureArm('right');
  }

  private captureArm(side: ArmSide): void {
    const upper = this.rig.getBone(side === 'left' ? 'leftUpperArm' : 'rightUpperArm');
    const lower = this.rig.getBone(side === 'left' ? 'leftLowerArm' : 'rightLowerArm');
    const hand = this.rig.getBone(side === 'left' ? 'leftHand' : 'rightHand');
    const hips = this.rig.getBone('hips');
    if (!upper || !lower || !hand || !hips || !upper.parent || !lower.parent) return;

    upper.updateWorldMatrix(true, true);
    const upperPosition = upper.getWorldPosition(this.startPosition);
    const hipsPosition = hips.getWorldPosition(this.endPosition);
    const sideSign = upperPosition.x >= hipsPosition.x ? 1 : -1;
    const baseUpper = this.base.get(side === 'left' ? 'leftUpperArm' : 'rightUpperArm');
    const baseLower = this.base.get(side === 'left' ? 'leftLowerArm' : 'rightLowerArm');
    if (!baseUpper || !baseLower) return;
    this.arms.set(side, { upper, lower, hand, baseUpper, baseLower, sideSign });
  }

  setEnabled(enabled: boolean): void {
    this.enabled = enabled;
  }

  setGesture(gesture: BodyGesture): void {
    this.gesture = gesture;
    // Keep the current enable/clip-ownership state; paused only changes the target profile.
  }

  update(deltaSeconds: number): void {
    const dt = THREE.MathUtils.clamp(deltaSeconds, 0, 0.05);
    this.phase += dt;
    this.blend = THREE.MathUtils.damp(this.blend, this.enabled ? 1 : 0, this.smoothing, dt);
    const alpha = 1 - Math.exp(-this.smoothing * dt);

    for (const [side, arm] of this.arms) {
      const profile = this.profileFor(side);
      this.aimSegment(arm.upper, arm.lower, arm.baseUpper, arm.sideSign * profile.upperOutward,
        profile.upperForward, this.blend, alpha);

      // Refresh descendant matrices after moving the upper arm before aiming the forearm.
      arm.upper.updateWorldMatrix(true, true);
      this.aimSegment(arm.lower, arm.hand, arm.baseLower, arm.sideSign * profile.forearmOutward,
        profile.forearmForward, this.blend, alpha);
    }
  }

  private profileFor(side: ArmSide): ArmPoseProfile {
    if (this.gesture !== 'speaking') return PROFILES[this.gesture];
    const sideOffset = side === 'right' ? 0 : 1.4;
    const wave = Math.sin(this.phase * 2.0 + sideOffset);
    return {
      upperOutward: 0.58 + (side === 'right' ? 0.17 : 0.07) * wave,
      upperForward: 0,
      forearmOutward: 0.14 + 0.08 * Math.max(0, wave),
      forearmForward: 0,
    };
  }

  private aimSegment(
    bone: THREE.Object3D,
    child: THREE.Object3D,
    baseLocal: THREE.Quaternion,
    outward: number,
    forward: number,
    blend: number,
    alpha: number,
  ): void {
    const parent = bone.parent;
    if (!parent) return;
    bone.updateWorldMatrix(true, true);
    bone.getWorldPosition(this.startPosition);
    child.getWorldPosition(this.endPosition);
    this.currentDirection.copy(this.endPosition).sub(this.startPosition);
    if (this.currentDirection.lengthSq() < 1e-8) return;
    this.currentDirection.normalize();

    this.targetDirection.set(outward, -1, forward).normalize();
    this.deltaRotation.setFromUnitVectors(this.currentDirection, this.targetDirection);
    bone.getWorldQuaternion(this.currentWorld);
    this.desiredWorld.copy(this.currentWorld).premultiply(this.deltaRotation);
    parent.getWorldQuaternion(this.parentWorldInverse).invert();
    this.desiredLocal.copy(this.parentWorldInverse).multiply(this.desiredWorld);

    this.blendedLocal.copy(baseLocal).slerp(this.desiredLocal, blend);
    bone.quaternion.slerp(this.blendedLocal, alpha);
  }

  reset(): void {
    for (const [name, quaternion] of this.base) {
      const node = this.rig.getBone(name as Parameters<HumanoidRig['getBone']>[0]);
      if (node) node.quaternion.copy(quaternion);
    }
    this.blend = 0;
  }
}
