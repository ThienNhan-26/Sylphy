'use strict';
const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('sylphyChat', Object.freeze({
  send: (text, sessionId) => ipcRenderer.invoke('chat:send', { text, sessionId }),
  history: (sessionId) => ipcRenderer.invoke('chat:history', { sessionId }),
  speak: (text) => ipcRenderer.invoke('chat:speak', { text }),
  voiceStatus: () => ipcRenderer.invoke('sylphy:voice:status'),
  minimize: () => ipcRenderer.invoke('sylphy:chat:minimize'),
  expand: () => ipcRenderer.invoke('sylphy:chat:expand'),
}));

