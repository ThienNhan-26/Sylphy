import { describe, expect, it } from 'vitest';
import { MAX_VRM_BYTES, validateVrmFile } from '../src/model-file';

describe('validateVrmFile', () => {
  it('accepts a non-empty VRM file', () => {
    expect(validateVrmFile({ name: 'sylphy.vrm', size: 1024 })).toEqual({ ok: true });
  });

  it('rejects files that are not VRM', () => {
    expect(validateVrmFile({ name: 'avatar.glb', size: 1024 }).ok).toBe(false);
  });

  it('rejects empty files', () => {
    expect(validateVrmFile({ name: 'empty.vrm', size: 0 }).ok).toBe(false);
  });

  it('rejects files larger than the safety limit', () => {
    expect(validateVrmFile({ name: 'large.vrm', size: MAX_VRM_BYTES + 1 }).ok).toBe(false);
  });
});
