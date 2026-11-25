import './style.css';

const avatarEmbedMode = new URLSearchParams(window.location.search).get('embed') === 'avatar';
if (avatarEmbedMode) document.documentElement.classList.add('avatar-embed');
import { VrmViewer } from './vrm-viewer';
import { validateVrmFile } from './model-file';
import type { PresentationFrameLike } from './behavior-vrm-runtime';
import { AmplitudeLipSyncController } from './lip-sync-controller';

const canvas = document.querySelector<HTMLCanvasElement>('#avatar-canvas')!;
const status = document.querySelector<HTMLDivElement>('#runtime-status')!;
const statusText = status.querySelector('span')!;
const fileInput = document.querySelector<HTMLInputElement>('#vrm-file')!;
const emptyState = document.querySelector<HTMLDivElement>('#empty-state')!;
const modelInfo = document.querySelector<HTMLDivElement>('#model-info')!;
const errorBox = document.querySelector<HTMLDivElement>('#error-box')!;
const characterValue = document.querySelector<HTMLElement>('#character-value')!;
const fpsValue = document.querySelector<HTMLElement>('#fps-value')!;
const fpsLabel = document.querySelector<HTMLElement>('#fps')!;
const resetButton = document.querySelector<HTMLButtonElement>('#reset-camera')!;
const unloadButton = document.querySelector<HTMLButtonElement>('#unload-model')!;
const expressionCapability = document.querySelector<HTMLElement>('#expression-capability')!;
const expressionButtons = [...document.querySelectorAll<HTMLButtonElement>('[data-mood]')];
const blinkButton = document.querySelector<HTMLButtonElement>('#blink-test')!;
const clearExpressionButton = document.querySelector<HTMLButtonElement>('#clear-expression')!;
const humanoidCapability = document.querySelector<HTMLElement>('#humanoid-capability')!;
const animationCapability = document.querySelector<HTMLElement>('#animation-capability')!;
const bodyPoseCapability = document.querySelector<HTMLElement>('#body-pose-capability')!;
const boneCount = document.querySelector<HTMLElement>('#bone-count')!;
const boneSummary = document.querySelector<HTMLElement>('#bone-summary')!;
const behaviorButtons = [...document.querySelectorAll<HTMLButtonElement>('[data-behavior]')];
const resumeBehaviorButton = document.querySelector<HTMLButtonElement>('#resume-behavior')!;
const gazeResetButton = document.querySelector<HTMLButtonElement>('#gaze-reset')!;
const clipCount = document.querySelector<HTMLElement>('#clip-count')!;
const clipList = document.querySelector<HTMLElement>('#clip-list')!;
const motionStatus = document.querySelector<HTMLElement>('#motion-status')!;

let currentObjectUrl: string | null = null;
const viewer = new VrmViewer(canvas, (fps) => {
  fpsValue.textContent = String(fps);
  fpsLabel.textContent = `${fps} FPS`;
});

window.addEventListener('sylphy:presentation-frame', (event: Event) => {
  const frame = (event as CustomEvent<PresentationFrameLike>).detail;
  try {
    viewer.applyPresentationFrame(frame);
  } catch (error) {
    errorBox.hidden = false;
    errorBox.textContent = error instanceof Error ? error.message : String(error);
  }
});

function setStatus(message: string, ready = false): void {
  statusText.textContent = message;
  status.classList.toggle('ready', ready);
}

function clearError(): void {
  errorBox.hidden = true;
  errorBox.textContent = '';
}

function releaseObjectUrl(): void {
  if (currentObjectUrl) URL.revokeObjectURL(currentObjectUrl);
  currentObjectUrl = null;
}

fileInput.addEventListener('change', async () => {
  const file = fileInput.files?.[0];
  if (!file) return;
  clearError();
  const check = validateVrmFile(file);
  if (!check.ok) {
    errorBox.hidden = false;
    errorBox.textContent = check.reason;
    return;
  }
  setStatus('Loading VRM modelâ€¦');
  emptyState.hidden = true;
  releaseObjectUrl();
  currentObjectUrl = URL.createObjectURL(file);
  try {
    const info = await viewer.load(currentObjectUrl, file.name);
    modelInfo.hidden = false;
    modelInfo.textContent = `${info.metaName} Â· VRM ${info.version} Â· ${info.expressions.length} expression controls detected`;
    characterValue.textContent = info.metaName;
    const clipNames = viewer.animationClipNames;
    clipCount.textContent = String(clipNames.length);
    clipList.replaceChildren();
    if (clipNames.length === 0) clipList.textContent = 'No embedded animation clips found in this model. Procedural motion remains available.';
    for (const clipName of clipNames) {
      const button = document.createElement('button');
      button.type = 'button'; button.textContent = clipName; button.className = 'clip-button';
      button.addEventListener('click', () => {
        if (!viewer.playAnimationClip(clipName, 'body')) {
          errorBox.hidden = false; errorBox.textContent = `Could not play animation clip: ${clipName}`;
        }
      });
      clipList.append(button);
    }
    const rig = info.humanoid;
    const rigReady = Boolean(rig?.complete);
    animationCapability.classList.toggle('ok', rigReady);
    animationCapability.classList.toggle('pending', !rigReady);
    const armPoseReady = Boolean(rig?.complete &&
      ['leftUpperArm', 'leftLowerArm', 'leftHand', 'rightUpperArm', 'rightLowerArm', 'rightHand']
        .every((bone) => rig.bones.some((entry) => entry.name === bone)));
    bodyPoseCapability.classList.toggle('ok', armPoseReady);
    bodyPoseCapability.classList.toggle('pending', !armPoseReady);
    for (const button of behaviorButtons) button.disabled = !rigReady;
    resumeBehaviorButton.disabled = !rigReady;
    gazeResetButton.disabled = !rigReady;
    humanoidCapability.classList.toggle('ok', rigReady);
    humanoidCapability.classList.toggle('pending', !rigReady);
    boneCount.textContent = rig ? `${rig.mapped}/${rig.total}` : 'Unavailable';
    boneSummary.textContent = !rig ? 'This VRM does not expose a humanoid rig.' : rig.criticalMissing.length ? `Missing core bones: ${rig.criticalMissing.join(', ')}` : `Core skeleton mapped. ${rig.missing.length} optional bones unavailable.`;
    resetButton.disabled = false;
    unloadButton.disabled = false;
    const expressionReady = info.expressions.length > 0;
    expressionCapability.classList.toggle('ok', expressionReady);
    expressionCapability.classList.toggle('pending', !expressionReady);
    for (const button of expressionButtons) button.disabled = !expressionReady;
    blinkButton.disabled = !expressionReady || !info.expressions.includes('blink');
    clearExpressionButton.disabled = !expressionReady;
    motionStatus.textContent = 'Preview behavior: IDLE Â· procedural motion active';
    setStatus('VRM model loaded', true);
  } catch (error) {
    viewer.unload();
    emptyState.hidden = false;
    errorBox.hidden = false;
    errorBox.textContent = error instanceof Error ? error.message : String(error);
    setStatus('Model load failed');
    releaseObjectUrl();
  }
  fileInput.value = '';
});

resetButton.addEventListener('click', () => viewer.resetCamera());
for (const button of expressionButtons) {
  button.addEventListener('click', () => {
    const mood = button.dataset.mood;
    viewer.setMood(mood === 'neutral' ? null : mood as 'happy' | 'sad' | 'surprised' | 'angry' | 'relaxed');
  });
}
blinkButton.addEventListener('click', () => {
  viewer.setBlink(true,);
  window.setTimeout(() => viewer.setBlink(false), 160);
});
clearExpressionButton.addEventListener('click', () => viewer.resetExpressions());
for (const button of behaviorButtons) {
  button.addEventListener('click', () => {
    const behavior = button.dataset.behavior as 'idle' | 'processing' | 'reacting' | 'speaking' | 'interrupted' | 'paused';
    viewer.applyBehavior(behavior);
    motionStatus.textContent = `Preview behavior: ${behavior.toUpperCase()}`;
  });
}
resumeBehaviorButton.addEventListener('click', () => {
  viewer.applyBehavior('idle');
  motionStatus.textContent = 'Preview behavior: IDLE';
});
gazeResetButton.addEventListener('click', () => {
  viewer.setGaze(0, 0);
  motionStatus.textContent = 'Gaze target reset';
});
unloadButton.addEventListener('click', () => {
  viewer.unload();
  releaseObjectUrl();
  emptyState.hidden = false;
  modelInfo.hidden = true;
  resetButton.disabled = true;
  unloadButton.disabled = true;
  characterValue.textContent = 'Not loaded';
  animationCapability.classList.remove('ok');
  animationCapability.classList.add('pending');
  bodyPoseCapability.classList.remove('ok');
  bodyPoseCapability.classList.add('pending');
  humanoidCapability.classList.remove('ok');
  humanoidCapability.classList.add('pending');
  boneCount.textContent = '--';
  boneSummary.textContent = 'Load a VRM to inspect its normalized skeleton.';
  motionStatus.textContent = 'Load a VRM to enable motion preview.';
  clipCount.textContent = '0';
  clipList.textContent = 'No embedded animation clips found in this model.';
  for (const button of behaviorButtons) button.disabled = true;
  resumeBehaviorButton.disabled = true;
  gazeResetButton.disabled = true;
  expressionCapability.classList.remove('ok');
  expressionCapability.classList.add('pending');
  for (const button of expressionButtons) button.disabled = true;
  blinkButton.disabled = true;
  clearExpressionButton.disabled = true;
  setStatus('Renderer ready', true);
});

window.addEventListener('beforeunload', () => {
  viewer.dispose();
  releaseObjectUrl();
});

setStatus('Renderer ready', true);


// Optional desktop shell controls. The renderer remains usable in a normal browser.
const desktopMinimize = document.querySelector<HTMLButtonElement>('#desktop-minimize');
const desktopHide = document.querySelector<HTMLButtonElement>('#desktop-hide');
const desktopTopmost = document.querySelector<HTMLButtonElement>('#desktop-topmost');
if (window.sylphyDesktop) {
  const controls = document.querySelector<HTMLElement>('.desktop-controls');
  if (controls) controls.style.display = 'flex';
  desktopMinimize?.addEventListener('click', () => { void window.sylphyDesktop?.minimize(); });
  desktopHide?.addEventListener('click', () => { void window.sylphyDesktop?.hide(); });
  desktopTopmost?.addEventListener('click', async () => {
    const enabled = await window.sylphyDesktop?.toggleAlwaysOnTop();
    desktopTopmost.textContent = enabled ? 'âŒƒ' : 'âŒ„';
    desktopTopmost.title = enabled ? 'Always on top: enabled' : 'Always on top: disabled';
  });
}


// Read-only Entity health projection. Entity remains the lifecycle authority.
import { EntityClient } from './bridge/entity-client';
const entityBadge = document.querySelector<HTMLElement>('#entity-connection');
if (entityBadge) {
  const entityClient = new EntityClient();
  let disposed = false;
  const poll = async () => {
    if (disposed) return;
    const snapshot = window.sylphyDesktop
      ? { ...(await window.sylphyDesktop.getEntityStatus()), checkedAt: new Date().toISOString() }
      : await entityClient.status();
    entityBadge.textContent = snapshot.connected
      ? `Entity: ${snapshot.lifecycle}`
      : 'Entity: offline';
    entityBadge.title = snapshot.error ?? `Last checked ${snapshot.checkedAt}`;
    entityBadge.classList.toggle('ready', snapshot.connected);
  };
  void poll();
  const interval = window.setInterval(() => { void poll(); }, 2500);
  window.addEventListener('beforeunload', () => {
    disposed = true;
    window.clearInterval(interval);
  }, { once: true });
}

// Entity owns behavior arbitration. The desktop renderer only polls and
// applies the latest immutable presentation frame; it never invents behavior.
const desktopBridge = window.sylphyDesktop;
if (desktopBridge?.getAvatarPresentation) {
  let disposed = false;
  let lastRevision = -1;
  let timer = 0;
  const pollPresentation = async () => {
    if (disposed) return;
    try {
      const frame = await desktopBridge.getAvatarPresentation();
      if (frame && frame.revision > lastRevision) {
        lastRevision = frame.revision;
        window.dispatchEvent(new CustomEvent('sylphy:presentation-frame', { detail: frame }));
      }
    } catch {
      // Avatar is optional: Entity continues independently if the bridge is down.
    }
    if (!disposed) timer = window.setTimeout(() => { void pollPresentation(); }, 100);
  };
  void pollPresentation();
  window.addEventListener('beforeunload', () => {
    disposed = true;
    window.clearTimeout(timer);
  }, { once: true });
}

// Amplitude lip-sync is a read-only projection of real Voice playback.
// Standalone desktop mode reads through Electron preload. Embedded mode
// receives validated snapshots from its same-origin Chat parent.
if (desktopBridge?.getVoiceStatus) {
  const lipSync = new AmplitudeLipSyncController((weight) => viewer.setMouthOpen(weight));
  let disposed = false;
  let timer = 0;

  const pollVoice = async () => {
    if (disposed) return;
    let speaking = false;
    try {
      const snapshot = await desktopBridge.getVoiceStatus();
      if (snapshot) {
        lipSync.update(snapshot);
        speaking = snapshot.enabled && snapshot.is_speaking && snapshot.state === 'playing';
      } else {
        lipSync.stop();
      }
    } catch {
      lipSync.stop();
    }
    if (!disposed) timer = window.setTimeout(() => { void pollVoice(); }, speaking ? 50 : 180);
  };

  void pollVoice();
  window.addEventListener('beforeunload', () => {
    disposed = true;
    window.clearTimeout(timer);
    lipSync.stop();
  }, { once: true });

} else if (avatarEmbedMode && window.parent !== window) {
  const lipSync = new AmplitudeLipSyncController((weight) => viewer.setMouthOpen(weight));
  let lastSequence = -1;
  let lastInstance = null as string | null;

  const onVoiceSnapshot = (event: MessageEvent) => {
    if (event.source !== window.parent) return;
    if (event.origin !== window.location.origin) return;

    const message = event.data;
    if (!message || message.type !== 'sylphy:voice-snapshot') return;

    const s = message.payload;
    if (!s || typeof s !== 'object'
      || typeof s.enabled !== 'boolean'
      || typeof s.is_speaking !== 'boolean'
      || typeof s.state !== 'string'
      || !Number.isFinite(s.amplitude)
      || !Number.isFinite(s.position_seconds)
      || !Number.isSafeInteger(s.sequence)
      || s.sequence < 0) {
      lipSync.stop();
      return;
    }

    if (s.entity_instance_id !== lastInstance) {
      lastInstance = s.entity_instance_id ?? null;
      lastSequence = -1;
    }

    if (s.sequence < lastSequence) return;
    lastSequence = s.sequence;

    lipSync.update(s);
    if (!s.enabled || !s.is_speaking || s.state !== 'playing') {
      lipSync.stop();
    }
  };

  window.addEventListener('message', onVoiceSnapshot);
  window.addEventListener('beforeunload', () => {
    window.removeEventListener('message', onVoiceSnapshot);
    lipSync.stop();
  }, { once: true });
}
// Desktop build ships the project avatar as a local public asset; browser preview
// continues to require an explicit user-selected file.
if (window.sylphyDesktop || avatarEmbedMode) {
  void fetch('./Sylphy.vrm').then(async (response) => {
    if (!response.ok) throw new Error(`Bundled Sylphy.vrm unavailable (${response.status})`);
    const file = new File([await response.blob()], 'Sylphy.vrm', { type: 'model/gltf-binary' });
    const transfer = new DataTransfer();
    transfer.items.add(file);
    fileInput.files = transfer.files;
    fileInput.dispatchEvent(new Event('change'));
  }).catch((error) => {
    errorBox.hidden = false;
    errorBox.textContent = error instanceof Error ? error.message : String(error);
  });
}

