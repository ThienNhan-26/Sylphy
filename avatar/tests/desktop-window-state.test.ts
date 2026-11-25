import { describe, expect, it } from 'vitest';
import state from '../desktop/window-state.cjs';

describe('Sanctuary window state', () => {
  it('resets legacy compact and preview bounds when the layout version changes', () => {
    expect(state.normalizeWindowState({
      width: 1600, height: 1000, x: 0, y: 0, positionVersion: 5,
    })).toEqual({ width: 1180, height: 780 });
  });

  it('restores freely resized geometry written by the current layout', () => {
    expect(state.normalizeWindowState({
      width: 740, height: 520, x: 120, y: 240, positionVersion: 6,
    })).toEqual({
      width: 740, height: 520, x: 120, y: 240, positionVersion: 6,
    });
  });

  it('allows compact companion dimensions below the previous fixed minimum', () => {
    expect(state.normalizeWindowState({
      width: 220, height: 240, x: 0, y: 0, positionVersion: 6,
    })).toEqual({
      width: 220, height: 240, x: 0, y: 0, positionVersion: 6,
    });
  });

  it('clamps malformed or undersized dimensions to the safe minimum', () => {
    expect(state.normalizeWindowState({
      width: Number.NaN, height: -Infinity, x: 'left', y: null, positionVersion: 6,
    })).toEqual({ width: 1180, height: 780, positionVersion: 6 });
    expect(state.normalizeWindowState({
      width: 40, height: 50, positionVersion: 6,
    })).toEqual({ width: 160, height: 160, positionVersion: 6 });
  });

  it('caps restored geometry to safe desktop bounds', () => {
    const result = state.normalizeWindowState({
      width: 12000, height: 9000, positionVersion: 6,
    });
    expect(result.width).toBe(7680);
    expect(result.height).toBe(4320);
  });
});
