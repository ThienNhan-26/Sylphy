'use strict';

const WINDOW_STATE_VERSION = 6;
const DEFAULT_BOUNDS = Object.freeze({ width: 1180, height: 780 });
const MIN_BOUNDS = Object.freeze({ width: 160, height: 160 });
const MAX_BOUNDS = Object.freeze({ width: 7680, height: 4320 });

function clampDimension(value, fallback, minimum, maximum) {
  if (!Number.isFinite(value)) return fallback;
  return Math.min(Math.max(Math.round(value), minimum), maximum);
}

function normalizeWindowState(value) {
  if (!value || value.positionVersion !== WINDOW_STATE_VERSION) {
    return { ...DEFAULT_BOUNDS };
  }
  return {
    width: clampDimension(value.width, DEFAULT_BOUNDS.width, MIN_BOUNDS.width, MAX_BOUNDS.width),
    height: clampDimension(value.height, DEFAULT_BOUNDS.height, MIN_BOUNDS.height, MAX_BOUNDS.height),
    ...(Number.isFinite(value.x) ? { x: Math.round(value.x) } : {}),
    ...(Number.isFinite(value.y) ? { y: Math.round(value.y) } : {}),
    positionVersion: WINDOW_STATE_VERSION,
  };
}

module.exports = { DEFAULT_BOUNDS, MIN_BOUNDS, MAX_BOUNDS, WINDOW_STATE_VERSION, normalizeWindowState };
