import { describe, expect, it } from 'vitest';
import { AmplitudeLipSyncController, type VoicePlaybackSnapshot } from '../src/lip-sync-controller';

function snapshot(overrides: Partial<VoicePlaybackSnapshot> = {}): VoicePlaybackSnapshot {
  return {
    enabled: true,
    state: 'ready',
    is_speaking: false,
    active_session_id: 'session-a',
    entity_instance_id: 'entity-instance-a',
    session_generation: 1,
    sequence: 2,
    position_seconds: 0,
    duration_seconds: 1,
    amplitude: 0,
    ...overrides,
  };
}

describe('AmplitudeLipSyncController', () => {
  it('keeps the mouth closed before real playback starts', () => {
    const writes: number[] = [];
    const sync = new AmplitudeLipSyncController((weight) => writes.push(weight));
    expect(sync.update(snapshot({ state: 'synthesizing', amplitude: 0.9 }))).toBe(true);
    expect(sync.currentWeight).toBe(0);
    expect(writes.at(-1)).toBe(0);
    expect(sync.update(snapshot({ state: 'ready', sequence: 3, amplitude: 0.9 }))).toBe(true);
    expect(sync.currentWeight).toBe(0);
  });

  it('opens the mouth from playback amplitude and smooths the change', () => {
    const sync = new AmplitudeLipSyncController(() => undefined, {
      noiseGate: 0.01,
      fullOpenAmplitude: 0.21,
      attackSeconds: 0.01,
    });
    expect(sync.update(snapshot({
      state: 'playing', is_speaking: true, sequence: 3,
      position_seconds: 0.05, amplitude: 0.21,
    }))).toBe(true);
    expect(sync.currentWeight).toBeGreaterThan(0.9);
    expect(sync.currentWeight).toBeLessThanOrEqual(1);
  });

  it('closes the mouth on completion without clearing unrelated expression channels', () => {
    const writes: number[] = [];
    const sync = new AmplitudeLipSyncController((weight) => writes.push(weight), {
      noiseGate: 0.01, fullOpenAmplitude: 0.21, attackSeconds: 0.01,
    });
    sync.update(snapshot({ state: 'playing', is_speaking: true, sequence: 3,
      position_seconds: 0.05, amplitude: 0.21 }));
    expect(sync.currentWeight).toBeGreaterThan(0.9);
    sync.update(snapshot({ state: 'completed', is_speaking: false, sequence: 4,
      position_seconds: 0.1, amplitude: 0 }));
    expect(sync.currentWeight).toBe(0);
    expect(writes.at(-1)).toBe(0);
  });

  it('stops on interruption and offline snapshots', () => {
    const sync = new AmplitudeLipSyncController(() => undefined, {
      noiseGate: 0.01, fullOpenAmplitude: 0.21, attackSeconds: 0.01,
    });
    sync.update(snapshot({ state: 'playing', is_speaking: true, sequence: 3,
      position_seconds: 0.05, amplitude: 0.21 }));
    expect(sync.currentWeight).toBeGreaterThan(0);
    sync.update(snapshot({ state: 'interrupted', is_speaking: false, sequence: 4,
      position_seconds: 0.05, amplitude: 0 }));
    expect(sync.currentWeight).toBe(0);
    sync.update(snapshot({ enabled: false, state: 'offline', is_speaking: false,
      sequence: 5, amplitude: 0 }));
    expect(sync.currentWeight).toBe(0);
  });

  it('rejects stale session generations and same-generation session swaps', () => {
    const sync = new AmplitudeLipSyncController(() => undefined, {
      noiseGate: 0.01, fullOpenAmplitude: 0.21, attackSeconds: 0.01,
    });
    sync.update(snapshot({ state: 'playing', is_speaking: true, sequence: 3,
      position_seconds: 0.05, amplitude: 0.21 }));
    const activeWeight = sync.currentWeight;
    expect(sync.update(snapshot({ session_generation: 0, active_session_id: 'old',
      state: 'playing', is_speaking: true, sequence: 99, position_seconds: 0.9, amplitude: 1 }))).toBe(false);
    expect(sync.currentWeight).toBe(activeWeight);
    expect(sync.update(snapshot({ active_session_id: 'rogue-session',
      state: 'playing', is_speaking: true, sequence: 100, position_seconds: 0.9, amplitude: 1 }))).toBe(false);
    expect(sync.currentWeight).toBe(activeWeight);
  });

  it('resets mouth state when a newer session begins, before it plays', () => {
    const sync = new AmplitudeLipSyncController(() => undefined, {
      noiseGate: 0.01, fullOpenAmplitude: 0.21, attackSeconds: 0.01,
    });
    sync.update(snapshot({ state: 'playing', is_speaking: true, sequence: 3,
      position_seconds: 0.05, amplitude: 0.21 }));
    expect(sync.currentWeight).toBeGreaterThan(0);
    sync.update(snapshot({ session_generation: 2, active_session_id: 'session-b',
      state: 'synthesizing', is_speaking: false, sequence: 1,
      position_seconds: 0, duration_seconds: 0, amplitude: 0 }));
    expect(sync.currentWeight).toBe(0);
  });

  it('ignores backwards playback positions and malformed snapshots', () => {
    const sync = new AmplitudeLipSyncController(() => undefined, {
      noiseGate: 0.01, fullOpenAmplitude: 0.21, attackSeconds: 0.01,
    });
    sync.update(snapshot({ state: 'playing', is_speaking: true, sequence: 3,
      position_seconds: 0.1, amplitude: 0.21 }));
    const activeWeight = sync.currentWeight;
    expect(sync.update(snapshot({ state: 'playing', is_speaking: true, sequence: 4,
      position_seconds: 0.02, amplitude: 0 }))).toBe(false);
    expect(sync.currentWeight).toBe(activeWeight);
    expect(sync.update({ ...snapshot(), amplitude: Number.NaN })).toBe(false);
    expect(sync.currentWeight).toBe(0);
  });
  it('ignores out-of-order event sequences within the current session', () => {
    const sync = new AmplitudeLipSyncController(() => undefined, {
      noiseGate: 0.01, fullOpenAmplitude: 0.21, attackSeconds: 0.01,
    });
    sync.update(snapshot({ state: 'playing', is_speaking: true, sequence: 5,
      position_seconds: 0.05, amplitude: 0.21 }));
    const activeWeight = sync.currentWeight;
    expect(sync.update(snapshot({ state: 'playing', is_speaking: true, sequence: 5,
      position_seconds: 0.09, amplitude: 0 }))).toBe(false);
    expect(sync.currentWeight).toBe(activeWeight);
  });

  it('resets generation scope when Entity restarts with a new instance identity', () => {
    const sync = new AmplitudeLipSyncController(() => undefined, {
      noiseGate: 0.01, fullOpenAmplitude: 0.21, attackSeconds: 0.01,
    });
    sync.update(snapshot({ state: 'playing', is_speaking: true, sequence: 5,
      position_seconds: 0.05, amplitude: 0.21 }));
    expect(sync.currentWeight).toBeGreaterThan(0);
    expect(sync.update(snapshot({ entity_instance_id: 'new-entity-instance',
      session_generation: 1, active_session_id: 'session-after-restart',
      state: 'playing', is_speaking: true, sequence: 1,
      position_seconds: 0.05, amplitude: 0.21 }))).toBe(true);
    expect(sync.currentWeight).toBeGreaterThan(0);
  });

  it('fails closed when Entity becomes offline even if generation resets', () => {
    const sync = new AmplitudeLipSyncController(() => undefined, {
      noiseGate: 0.01, fullOpenAmplitude: 0.21, attackSeconds: 0.01,
    });
    sync.update(snapshot({ state: 'playing', is_speaking: true, sequence: 5,
      position_seconds: 0.05, amplitude: 0.21 }));
    expect(sync.currentWeight).toBeGreaterThan(0);
    sync.update(snapshot({ enabled: false, state: 'offline', entity_instance_id: null,
      session_generation: 0, active_session_id: null, sequence: 0,
      position_seconds: 0, duration_seconds: 0, amplitude: 0 }));
    expect(sync.currentWeight).toBe(0);
  });

});
