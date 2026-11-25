export type ModelFileCheck =
  | { ok: true }
  | { ok: false; reason: string };

export const MAX_VRM_BYTES = 250 * 1024 * 1024;

export function validateVrmFile(file: Pick<File, 'name' | 'size'>): ModelFileCheck {
  if (!file.name.toLowerCase().endsWith('.vrm')) {
    return { ok: false, reason: 'Please select a .vrm file.' };
  }
  if (file.size <= 0) {
    return { ok: false, reason: 'The selected VRM file is empty.' };
  }
  if (file.size > MAX_VRM_BYTES) {
    return { ok: false, reason: 'The VRM file exceeds the 250 MiB safety limit.' };
  }
  return { ok: true };
}
