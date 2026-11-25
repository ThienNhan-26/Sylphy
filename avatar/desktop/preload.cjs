const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('sylphyDesktop', Object.freeze({
  minimize: () => ipcRenderer.invoke('sylphy:window:minimize'),
  hide: () => ipcRenderer.invoke('sylphy:window:close'),
  toggleAlwaysOnTop: () => ipcRenderer.invoke('sylphy:window:toggle-topmost'),
  getWindowState: () => ipcRenderer.invoke('sylphy:window:get-state'),
  getEntityStatus: () => ipcRenderer.invoke('sylphy:entity:status'),
  getVoiceStatus: () => ipcRenderer.invoke('sylphy:voice:status'),
  getAvatarPresentation: () => ipcRenderer.invoke('sylphy:avatar:presentation'),

  // Narrow API for right-button drag desktop positioning.
  beginWindowMove: (screenX, screenY) => {
    ipcRenderer.send('sylphy:window:move-start', screenX, screenY);
  },
  moveWindow: (screenX, screenY) => {
    ipcRenderer.send('sylphy:window:move', screenX, screenY);
  },
  endWindowMove: () => {
    ipcRenderer.send('sylphy:window:move-end');
  },
}));
