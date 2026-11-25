import * as THREE from 'three';

export const HUMANOID_BONES = [
  'hips', 'spine', 'chest', 'upperChest', 'neck', 'head',
  'leftEye', 'rightEye', 'jaw',
  'leftShoulder', 'leftUpperArm', 'leftLowerArm', 'leftHand',
  'rightShoulder', 'rightUpperArm', 'rightLowerArm', 'rightHand',
  'leftUpperLeg', 'leftLowerLeg', 'leftFoot', 'leftToes',
  'rightUpperLeg', 'rightLowerLeg', 'rightFoot', 'rightToes',
] as const;

export type HumanoidBoneName = typeof HUMANOID_BONES[number];
export interface HumanoidSource { getNormalizedBoneNode(name: string): THREE.Object3D | null; }
export interface BoneDescriptor { name: HumanoidBoneName; node: THREE.Object3D; parentName: string | null; depth: number; }
export interface HumanoidRigReport { total: number; mapped: number; missing: HumanoidBoneName[]; criticalMissing: HumanoidBoneName[]; complete: boolean; bones: BoneDescriptor[]; }
/** Core humanoid bones needed for a usable full-body humanoid rig. */
export const REQUIRED_HUMANOID_BONES: readonly HumanoidBoneName[] = [
  'hips', 'spine', 'head',
  'leftUpperLeg', 'leftLowerLeg', 'leftFoot',
  'rightUpperLeg', 'rightLowerLeg', 'rightFoot',
  'leftUpperArm', 'leftLowerArm', 'leftHand',
  'rightUpperArm', 'rightLowerArm', 'rightHand',
];
function getDepth(node: THREE.Object3D): number {
  let depth = 0; let current: THREE.Object3D | null = node;
  while (current?.parent) { depth += 1; current = current.parent; }
  return depth;
}
/** Inspects normalized VRM humanoid nodes without changing transforms. */
export class HumanoidRig {
  private readonly mapped = new Map<HumanoidBoneName, THREE.Object3D>();
  constructor(private readonly humanoid: HumanoidSource) {
    for (const name of HUMANOID_BONES) {
      const node = humanoid.getNormalizedBoneNode(name);
      if (node) this.mapped.set(name, node);
    }
  }
  getBone(name: HumanoidBoneName): THREE.Object3D | null { return this.mapped.get(name) ?? null; }
  hasBone(name: HumanoidBoneName): boolean { return this.mapped.has(name); }
  get report(): HumanoidRigReport {
    const bones = [...this.mapped.entries()].map(([name, node]) => ({
      name, node, parentName: node.parent?.name ?? null, depth: getDepth(node),
    }));
    const missing = HUMANOID_BONES.filter((name) => !this.mapped.has(name));
    const criticalMissing = REQUIRED_HUMANOID_BONES.filter((name) => !this.mapped.has(name));
    return { total: HUMANOID_BONES.length, mapped: this.mapped.size, missing, criticalMissing, complete: criticalMissing.length === 0, bones };
  }
  refresh(): HumanoidRig { return new HumanoidRig(this.humanoid); }
}
