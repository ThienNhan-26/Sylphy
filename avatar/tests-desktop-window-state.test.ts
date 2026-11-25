import { describe, expect, it } from 'vitest';
import state from '../desktop/window-state.cjs';

describe('desktop window state migration', () => {
  it('resets legacy preview-sized bounds when the layout version changes', () => {
    expect(state.normalizeWindowState({
      width: 1600,
      height: 1000,
      x: 0,
      y: 0,
      positionVersion: 2,
    })).toEqual({ width: 360, height: 520 });
  });

  it('restores bounded geometry written by the current layout', () => {
    expect(state.normalizeWindowState({
      width: 380,
      height: 560,
      x: 120,
      y: 240,
      positionVersion: 3,
    })).toEqual({
      width: 380,
      height: 560,
      x: 120,
      y: 240,
      positionVersion: 3,
    });
  });

  it('caps current-version dimensions to the compact companion limits', () => {
    const result = state.normalizeWindowState({
      width: 2400,
      height: 1600,
      x: 0,
      y: 0,
      positionVersion: 3,
    });
    expect(result.width).toBe(420);
    expect(result.height).toBe(640);
  });

  it('uses safe defaults for malformed dimensions and ignores invalid coordinates', () => {
    expect(state.normalizeWindowState({
      width: Number.NaN,
      height: -Infinity,
      x: 'left',
      y: null,
      positionVersion: 3,
    })).toEqual({
      width: 360,
      height: 520,
      positionVersion: 3,
    });
  });
});
