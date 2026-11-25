import { describe, expect, it } from 'vitest';
import * as THREE from 'three';
import { HumanoidRig } from '../src/humanoid-rig';
import { IdleMotionController } from '../src/idle-motion';
import { HeadGazeController } from '../src/head-gaze-controller';
import { AnimationClipRegistry } from '../src/animation-clip-registry';
import { BehaviorVrmRuntime } from '../src/behavior-vrm-runtime';

function makeRig() {
  const nodes: Record<string, THREE.Object3D> = {};
  for (const name of ['hips', 'spine', 'chest', 'upperChest', 'neck', 'head', 'leftEye', 'rightEye']) {
    nodes[name] = new THREE.Object3D();
  }
  nodes.hips.add(nodes.spine); nodes.spine.add(nodes.chest); nodes.chest.add(nodes.upperChest);
  nodes.upperChest.add(nodes.neck); nodes.neck.add(nodes.head); nodes.head.add(nodes.leftEye, nodes.rightEye);
  return new HumanoidRig({ getNormalizedBoneNode: (name) => nodes[name] ?? null });
}

describe('IdleMotionController', () => {
  it('adds bounded procedural motion relative to bind pose', () => {
    const rig = makeRig(); const spine = rig.getBone('spine')!; const base = spine.quaternion.clone();
    const idle = new IdleMotionController(rig); idle.update(0.05);
    expect(spine.quaternion.equals(base)).toBe(false);
    idle.setEnabled(false);
    for (let i = 0; i < 30; i += 1) idle.update(0.05);
    expect(spine.quaternion.angleTo(base)).toBeLessThan(0.01);
  });
  it('clamps intensity and can reset to bind pose', () => {
    const rig = makeRig(); const head = rig.getBone('head')!; const base = head.quaternion.clone();
    const idle = new IdleMotionController(rig); idle.setIntensity(3); idle.update(0.05); idle.reset();
    expect(head.quaternion.angleTo(base)).toBeLessThan(1e-8);
  });
});

describe('HeadGazeController', () => {
  it('moves head and eyes smoothly toward a bounded target', () => {
    const rig = makeRig(); const head = rig.getBone('head')!; const base = head.quaternion.clone();
    const gaze = new HeadGazeController(rig); gaze.setTarget(8, -8);
    for (let i = 0; i < 30; i += 1) gaze.update(0.05);
    expect(head.quaternion.angleTo(base)).toBeGreaterThan(0.05);
    expect(Number.isFinite(head.quaternion.x)).toBe(true);
  });
  it('returns to the neutral pose when disabled', () => {
    const rig = makeRig(); const head = rig.getBone('head')!; const base = head.quaternion.clone();
    const gaze = new HeadGazeController(rig); gaze.setTarget(0.4, 0.2);
    for (let i = 0; i < 20; i += 1) gaze.update(0.05);
    gaze.setEnabled(false);
    for (let i = 0; i < 40; i += 1) gaze.update(0.05);
    expect(head.quaternion.angleTo(base)).toBeLessThan(0.01);
  });
});

describe('AnimationClipRegistry', () => {
  it('registers, resolves aliases, plays and interrupts clips', () => {
    const root = new THREE.Object3D();
    const track = new THREE.NumberKeyframeTrack('.position[x]', [0, 1], [0, 1]);
    const clip = new THREE.AnimationClip('idle', 1, [track]);
    const registry = new AnimationClipRegistry(root, [clip]);
    registry.alias('behavior.idle', 'idle');
    expect(registry.has('behavior.idle')).toBe(true);
    expect(registry.play('behavior.idle')).toBe(true);
    expect(registry.current).toBe('idle');
    registry.stop('idle', 0);
    expect(registry.current).toBeNull();
    registry.dispose();
  });
  it('tracks clip playback independently by body region channel', () => {
    const root = new THREE.Object3D();
    const idle = new THREE.AnimationClip('idle', 1, [new THREE.NumberKeyframeTrack('.position[x]', [0, 1], [0, 1])]);
    const talk = new THREE.AnimationClip('talk', 1, [new THREE.NumberKeyframeTrack('.position[y]', [0, 1], [0, 1])]);
    const registry = new AnimationClipRegistry(root, [idle, talk]);
    expect(registry.play('idle', { channel: 'body' })).toBe(true);
    expect(registry.play('talk', { channel: 'mouth' })).toBe(true);
    expect(registry.activeChannels.get('body')).toBe('idle');
    expect(registry.activeChannels.get('mouth')).toBe('talk');
    registry.dispose();
  });
  it('safely rejects unknown clips', () => {
    const registry = new AnimationClipRegistry(new THREE.Object3D());
    expect(registry.play('missing')).toBe(false);
    registry.dispose();
  });
});

describe('BehaviorVrmRuntime', () => {
  it('maps Entity behavior states into procedural VRM controls', () => {
    const rig = makeRig();
    const clips = new AnimationClipRegistry(new THREE.Object3D());
    const runtime = new BehaviorVrmRuntime(rig, clips, null);
    runtime.applyBehavior('processing');
    expect(runtime.currentBehavior).toBe('processing');
    runtime.update(0.016);
    runtime.applyBehavior('paused');
    expect(runtime.currentBehavior).toBe('paused');
    runtime.applyBehavior('idle');
    expect(runtime.currentBehavior).toBe('idle');
    runtime.dispose();
  });
  it('accepts renderer-neutral presentation frames and ignores absent clip assets', () => {
    const rig = makeRig(); const clips = new AnimationClipRegistry(new THREE.Object3D());
    const runtime = new BehaviorVrmRuntime(rig, clips, null);
    runtime.applyPresentationFrame({ behavior: 'speaking', commands: [{
      action: 'start', command_id: 'voice:1:mouth', owner: 'voice:1', behavior: 'speaking',
      clip: 'speaking.lipsync', region: 'mouth', priority: 70, loop: true,
    }] });
    expect(runtime.currentBehavior).toBe('speaking');
    runtime.applyPresentationFrame({ behavior: 'idle', commands: [] });
    expect(runtime.currentBehavior).toBe('idle');
    runtime.dispose();
  });
  it('assigns semantic behavior clips to the body channel and releases them on pause', () => {
    const rig = makeRig();
    const root = new THREE.Object3D();
    const spine = new THREE.Object3D(); spine.name = 'spine'; root.add(spine);
    const clip = new THREE.AnimationClip('behavior.speaking', 1, [
      new THREE.NumberKeyframeTrack('spine.position[x]', [0, 1], [0, 0.02]),
    ]);
    const clips = new AnimationClipRegistry(root, [clip]);
    const runtime = new BehaviorVrmRuntime(rig, clips, null);
    runtime.applyBehavior('speaking');
    expect(clips.activeChannels.get('body')).toBe('behavior.speaking');
    runtime.applyBehavior('paused');
    expect(clips.activeChannels.has('body')).toBe(false);
    runtime.update(1 / 60);
    runtime.dispose();
  });
});

describe('AutomaticBlinkController', () => {
  it('schedules a natural blink from delta time and disables cleanly', async () => {
    const { AutomaticBlinkController } = await import('../src/automatic-blink-controller');
    const calls: boolean[] = [];
    const expressionStub = {
      has: (name: string) => name === 'blink',
      setBlink: (closed: boolean) => calls.push(closed),
    } as unknown as import('../src/expression-controller').VrmExpressionController;
    const blink = new AutomaticBlinkController(expressionStub);
    for (let i = 0; i < 80; i += 1) blink.update(0.05);
    expect(calls).toContain(true);
    for (let i = 0; i < 8; i += 1) blink.update(0.05);
    expect(calls).toContain(false);
    blink.setEnabled(false);
    expect(calls[calls.length - 1]).toBe(false);
  });
});


function makeArmRig() {
  const hips = new THREE.Object3D(); hips.position.set(0, 1, 0);
  const spine = new THREE.Object3D(); hips.add(spine);
  const leftUpperArm = new THREE.Object3D(); leftUpperArm.position.set(-0.2, 0.3, 0); spine.add(leftUpperArm);
  const leftLowerArm = new THREE.Object3D(); leftLowerArm.position.set(-0.42, 0, 0); leftUpperArm.add(leftLowerArm);
  const leftHand = new THREE.Object3D(); leftHand.position.set(-0.34, 0, 0); leftLowerArm.add(leftHand);
  const rightUpperArm = new THREE.Object3D(); rightUpperArm.position.set(0.2, 0.3, 0); spine.add(rightUpperArm);
  const rightLowerArm = new THREE.Object3D(); rightLowerArm.position.set(0.42, 0, 0); rightUpperArm.add(rightLowerArm);
  const rightHand = new THREE.Object3D(); rightHand.position.set(0.34, 0, 0); rightLowerArm.add(rightHand);
  const nodes: Record<string, THREE.Object3D> = {
    hips, spine, leftUpperArm, leftLowerArm, leftHand,
    rightUpperArm, rightLowerArm, rightHand,
  };
  const rig = new HumanoidRig({ getNormalizedBoneNode: (name) => nodes[name] ?? null });
  return { rig, hips, leftUpperArm, leftLowerArm, leftHand, rightUpperArm, rightLowerArm, rightHand };
}

describe('BodyPoseController', () => {
  it('moves upper arms and forearms out of a horizontal T-pose', async () => {
    const { BodyPoseController } = await import('../src/body-pose-controller');
    const model = makeArmRig();
    const originalLeft = model.leftUpperArm.quaternion.clone();
    const originalRight = model.rightUpperArm.quaternion.clone();
    const body = new BodyPoseController(model.rig);
    for (let i = 0; i < 120; i += 1) body.update(1 / 60);

    model.hips.updateWorldMatrix(true, true);
    const leftUpperStart = model.leftUpperArm.getWorldPosition(new THREE.Vector3());
    const leftElbow = model.leftLowerArm.getWorldPosition(new THREE.Vector3());
    const leftWrist = model.leftHand.getWorldPosition(new THREE.Vector3());
    const rightUpperStart = model.rightUpperArm.getWorldPosition(new THREE.Vector3());
    const rightElbow = model.rightLowerArm.getWorldPosition(new THREE.Vector3());
    const rightWrist = model.rightHand.getWorldPosition(new THREE.Vector3());
    const leftUpperDirection = leftElbow.sub(leftUpperStart).normalize();
    const rightUpperDirection = rightElbow.sub(rightUpperStart).normalize();
    const leftForearmDirection = leftWrist.sub(model.leftLowerArm.getWorldPosition(new THREE.Vector3())).normalize();
    const rightForearmDirection = rightWrist.sub(model.rightLowerArm.getWorldPosition(new THREE.Vector3())).normalize();

    expect(leftUpperDirection.y).toBeLessThan(-0.65);
    expect(rightUpperDirection.y).toBeLessThan(-0.65);
    expect(leftUpperDirection.x).toBeLessThan(0);
    expect(rightUpperDirection.x).toBeGreaterThan(0);
    expect(leftForearmDirection.y).toBeLessThan(-0.65);
    expect(rightForearmDirection.y).toBeLessThan(-0.65);
    expect(model.leftUpperArm.quaternion.angleTo(originalLeft)).toBeGreaterThan(0.1);
    expect(model.rightUpperArm.quaternion.angleTo(originalRight)).toBeGreaterThan(0.1);

    body.reset();
    expect(model.leftUpperArm.quaternion.angleTo(originalLeft)).toBeLessThan(1e-6);
    expect(model.rightUpperArm.quaternion.angleTo(originalRight)).toBeLessThan(1e-6);
  });

  it('gives behaviors distinct arm profiles and freezes variation while paused', async () => {
    const { BodyPoseController } = await import('../src/body-pose-controller');
    const model = makeArmRig();
    const body = new BodyPoseController(model.rig);
    const settle = (frames = 90) => {
      for (let i = 0; i < frames; i += 1) body.update(1 / 60);
    };

    body.setGesture('idle'); settle();
    const idlePose = model.rightUpperArm.quaternion.clone();
    body.setGesture('processing'); settle();
    const processingPose = model.rightUpperArm.quaternion.clone();
    expect(processingPose.angleTo(idlePose)).toBeGreaterThan(0.08);

    body.setGesture('reacting'); settle();
    const reactingPose = model.rightUpperArm.quaternion.clone();
    expect(reactingPose.angleTo(processingPose)).toBeGreaterThan(0.08);

    body.setGesture('speaking'); settle(15);
    const speakingPoseA = model.rightUpperArm.quaternion.clone();
    settle(30);
    const speakingPoseB = model.rightUpperArm.quaternion.clone();
    expect(speakingPoseB.angleTo(speakingPoseA)).toBeGreaterThan(0.005);

    body.setGesture('paused'); settle();
    const pausedPoseA = model.rightUpperArm.quaternion.clone();
    settle();
    const pausedPoseB = model.rightUpperArm.quaternion.clone();
    expect(pausedPoseB.angleTo(pausedPoseA)).toBeLessThan(0.01);
  });
});
