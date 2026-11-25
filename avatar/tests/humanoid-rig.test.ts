import { describe, expect, it } from 'vitest';
import * as THREE from 'three';
import { HUMANOID_BONES, REQUIRED_HUMANOID_BONES, HumanoidRig } from '../src/humanoid-rig';
describe('HumanoidRig', () => {
  it('maps normalized bones without changing transforms', () => {
    const hips = new THREE.Object3D(); const spine = new THREE.Object3D(); const head = new THREE.Object3D();
    hips.add(spine); spine.add(head); const before = head.quaternion.clone();
    const nodes: Record<string, THREE.Object3D> = { hips, spine, head };
    const rig = new HumanoidRig({ getNormalizedBoneNode: (name) => nodes[name] ?? null });
    expect(rig.getBone('hips')).toBe(hips); expect(rig.getBone('head')).toBe(head);
    expect(head.quaternion.equals(before)).toBe(true);
  });
  it('reports core mapping and optional missing bones', () => {
    const nodes: Record<string, THREE.Object3D> = Object.fromEntries(REQUIRED_HUMANOID_BONES.map((name) => [name, new THREE.Object3D()]));
    const rig = new HumanoidRig({ getNormalizedBoneNode: (name) => nodes[name] ?? null });
    expect(rig.report.complete).toBe(true); expect(rig.report.mapped).toBe(REQUIRED_HUMANOID_BONES.length); expect(rig.report.total).toBe(HUMANOID_BONES.length);
    expect(rig.report.criticalMissing).toEqual([]); expect(rig.report.missing).toContain('leftEye');
  });
  it('marks rig incomplete when a core bone is missing', () => {
    const rig = new HumanoidRig({ getNormalizedBoneNode: (name) => ['hips', 'head'].includes(name) ? new THREE.Object3D() : null });
    expect(rig.report.complete).toBe(false); expect(rig.report.criticalMissing).toContain('leftUpperLeg');
  });
  it('reports hierarchy metadata', () => {
    const parent = new THREE.Object3D(); const child = new THREE.Object3D(); parent.add(child);
    const rig = new HumanoidRig({ getNormalizedBoneNode: (name) => name === 'hips' ? parent : name === 'spine' ? child : null });
    expect(rig.report.bones.find((bone) => bone.name === 'spine')?.depth).toBe(1);
    expect(rig.report.bones.find((bone) => bone.name === 'spine')?.parentName).toBe(parent.name);
  });
  it('refreshes mapping without mutating the previous snapshot', () => {
    const first = new THREE.Object3D(); const second = new THREE.Object3D(); let current = first;
    const rig = new HumanoidRig({ getNormalizedBoneNode: (name) => name === 'hips' ? current : null }); current = second;
    expect(rig.refresh().getBone('hips')).toBe(second); expect(rig.getBone('hips')).toBe(first);
  });
});
