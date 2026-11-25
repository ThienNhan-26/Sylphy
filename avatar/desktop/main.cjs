
const {
  app,
  BrowserWindow,
  ipcMain,
  screen,
  shell,
  globalShortcut,
  Tray,
  Menu,
  nativeImage,
} = require('electron');

const fs = require('node:fs');
const path = require('node:path');

const APP_ID = 'com.sylphy.avatar';
const {
  DEFAULT_BOUNDS,
  MAX_BOUNDS,
  WINDOW_STATE_VERSION,
  normalizeWindowState,
} = require('./window-state.cjs');

let win = null;
let quitting = false;
let saveTimer = null;
let tray = null;
let chatWin = null;

function statePath() {
  return path.join(
    app.getPath('userData'),
    'window-state.json',
  );
}

function readState() {
  try {
    const parsed = JSON.parse(
      fs.readFileSync(statePath(), 'utf8'),
    );
    return normalizeWindowState(parsed);
  } catch {
    // First launch or invalid state.
    return { ...DEFAULT_BOUNDS };
  }
}

function saveState() {
  if (!win || win.isDestroyed()) return;

  try {
    const bounds = win.getBounds();

    fs.mkdirSync(
      path.dirname(statePath()),
      { recursive: true },
    );

    fs.writeFileSync(
      statePath(),
      JSON.stringify({ ...bounds, positionVersion: WINDOW_STATE_VERSION }, null, 2),
      'utf8',
    );
  } catch (error) {
    console.error(
      '[SYLPHY] Could not save window state:',
      error,
    );
  }
}

function scheduleSaveState() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(saveState, 350);
}

const DESKTOP_TRANSPARENT_CSS = `
  :root {
    background: transparent !important;
    color-scheme: dark;
  }

  *,
  *::before,
  *::after {
    box-sizing: border-box;
  }

  html,
  body {
    width: 100%;
    height: 100%;
    min-width: 0 !important;
    min-height: 0 !important;
    margin: 0 !important;
    padding: 0 !important;
    overflow: hidden !important;
    background: transparent !important;
    background-color: transparent !important;
    background-image: none !important;
  }

  #app {
    display: block !important;
    width: 100%;
    height: 100%;
    min-height: 0 !important;
    margin: 0;
    padding: 0;
    background: transparent !important;
  }

  .topbar,
  .stage-overlay,
  .stage-footer,
  .inspector,
  .footer {
    display: none !important;
  }

  main {
    display: block !important;
    position: relative;
    width: 100% !important;
    height: 100% !important;
    min-height: 0 !important;
    margin: 0 !important;
    padding: 0 !important;
    background: transparent !important;
  }

  .stage {
    position: absolute !important;
    inset: 0 !important;
    width: 100% !important;
    height: 100% !important;
    min-height: 0 !important;
    margin: 0 !important;
    padding: 0 !important;
    border: none !important;
    border-radius: 0 !important;
    overflow: hidden !important;
    background: transparent !important;
    background-image: none !important;
    box-shadow: none !important;
    -webkit-app-region: no-drag;
  }

  #avatar-canvas,
  .stage canvas {
    position: absolute !important;
    inset: 0 !important;
    display: block !important;
    width: 100% !important;
    height: 100% !important;
    margin: 0 !important;
    padding: 0 !important;
    border: none !important;
    outline: none !important;
    background: transparent !important;
    background-color: transparent !important;
    -webkit-app-region: no-drag;
    touch-action: none;
  }

  .brand,
  .brand-mark,
  .status,
  .desktop-controls,
  .model-info,
  .error-box,
  .empty-state {
    display: none !important;
  }

  ::-webkit-scrollbar {
    display: none;
  }
`;


function readControlToken() {
  const lappdata = process.env.LOCALAPPDATA ||
    path.join(require('os').homedir(), 'AppData', 'Local');
  const tokenPath = path.join(lappdata, 'SYLPHY', 'EntityRuntime', 'control.token');
  try { return fs.readFileSync(tokenPath, 'utf8').trim(); } catch { return null; }
}

let lastExpandedBounds = { width: DEFAULT_BOUNDS.width, height: DEFAULT_BOUNDS.height };
let chatSaveTimer = null;

function saveChatWindowState() {
  if (!chatWin || chatWin.isDestroyed()) return;
  try {
    const bounds = chatWin.getBounds();
    fs.mkdirSync(path.dirname(statePath()), { recursive: true });
    fs.writeFileSync(statePath(), JSON.stringify({ ...bounds, positionVersion: WINDOW_STATE_VERSION }, null, 2), 'utf8');
  } catch (error) {
    console.error('[SYLPHY] Could not save Sanctuary window state:', error);
  }
}

function scheduleSaveChatWindowState() {
  clearTimeout(chatSaveTimer);
  chatSaveTimer = setTimeout(saveChatWindowState, 300);
}

function createChatWindow() {
  if (chatWin && !chatWin.isDestroyed()) { chatWin.show(); chatWin.focus(); return chatWin; }
  const saved = readState();
  const hasPosition = Number.isFinite(saved.x) && Number.isFinite(saved.y);
  const display = hasPosition ? screen.getDisplayMatching(saved) : screen.getPrimaryDisplay();
  const area = display.workArea;
  const width = Math.min(saved.width || DEFAULT_BOUNDS.width, area.width);
  const height = Math.min(saved.height || DEFAULT_BOUNDS.height, area.height);
  const x = Math.min(Math.max(hasPosition ? saved.x : area.x + Math.round((area.width - width) / 2), area.x), area.x + area.width - width);
  const y = Math.min(Math.max(hasPosition ? saved.y : area.y + Math.round((area.height - height) / 2), area.y), area.y + area.height - height);
  chatWin = new BrowserWindow({
    x, y, width, height, minWidth: 160, minHeight: 160,
    title: 'SYLPHY · Lavender Sanctuary', backgroundColor: '#f7f3ea',
    frame: true, transparent: false, alwaysOnTop: false,
    resizable: true, autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, 'chat-preload.cjs'),
      contextIsolation: true, nodeIntegration: false, sandbox: false,
    },
  });
  if (width > 560 && height > 300) lastExpandedBounds = { width, height };
  void chatWin.loadFile(path.join(__dirname, 'chat.html'));
  ipcMain.removeHandler('sylphy:chat:minimize');
  ipcMain.handle('sylphy:chat:minimize', () => { chatWin?.minimize(); return true; });
  ipcMain.removeHandler('sylphy:chat:expand');
  ipcMain.handle('sylphy:chat:expand', () => {
    if (!chatWin || chatWin.isDestroyed()) return false;
    const display = screen.getDisplayMatching(chatWin.getBounds());
    const area = display.workArea;
    const width = Math.min(Math.max(lastExpandedBounds.width, 600), area.width);
    const height = Math.min(Math.max(lastExpandedBounds.height, 420), area.height);
    chatWin.setMinimumSize(160, 160);
    chatWin.setSize(width, height, true);
    chatWin.center();
    chatWin.show();
    chatWin.focus();
    return true;
  });
  chatWin.on('resize', () => {
    const bounds = chatWin?.getBounds();
    if (bounds && bounds.width > 560 && bounds.height > 300) lastExpandedBounds = { width: bounds.width, height: bounds.height };
    scheduleSaveChatWindowState();
  });
  chatWin.on('move', scheduleSaveChatWindowState);
  chatWin.on('closed', () => { clearTimeout(chatSaveTimer); chatWin = null; });
  return chatWin;
}
function createWindow() {
  if (win && !win.isDestroyed()) {
    win.show();
    return win;
  }

  const saved = readState();

  // Version 1 saved positions were from the old preview layout.
  // On this upgrade, place Sylphy at the bottom-right once.
  const hasSavedPosition =
    saved.positionVersion === WINDOW_STATE_VERSION &&
    Number.isFinite(saved.x) &&
    Number.isFinite(saved.y);

  const display = hasSavedPosition
    ? screen.getDisplayMatching(saved)
    : screen.getPrimaryDisplay();

  const area = display.workArea;

  const width = Math.min(
    Math.max(saved.width || DEFAULT_BOUNDS.width, 220),
    MAX_BOUNDS.width,
    area.width,
  );

  const height = Math.min(
    Math.max(saved.height || DEFAULT_BOUNDS.height, 320),
    MAX_BOUNDS.height,
    area.height,
  );

  const x = Math.min(
    Math.max(
      hasSavedPosition ? saved.x : area.x + area.width - width - 24,
      area.x,
    ),
    area.x + area.width - width,
  );

  const y = Math.min(
    Math.max(
      hasSavedPosition ? saved.y : area.y + area.height - height - 24,
      area.y,
    ),
    area.y + area.height - height,
  );

  win = new BrowserWindow({
    x,
    y,
    width,
    height,

    minWidth: 220,
    minHeight: 320,
    maxWidth: MAX_BOUNDS.width,
    maxHeight: MAX_BOUNDS.height,

    frame: false,
    transparent: true,
    backgroundColor: '#00000000',
    hasShadow: false,

    alwaysOnTop: true,
    movable: true,
    resizable: true,
    minimizable: false,
    maximizable: false,
    fullscreenable: false,

    show: false,
    autoHideMenuBar: true,

    webPreferences: {
      preload: path.join(
        __dirname,
        'preload.cjs',
      ),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });

  win.setAlwaysOnTop(true, 'floating');

  win.webContents.on(
    'did-finish-load',
    async () => {
      if (!win || win.isDestroyed()) return;

      try {
        await win.webContents.insertCSS(
          DESKTOP_TRANSPARENT_CSS,
        );

        console.log(
          '[SYLPHY] Transparent desktop presentation applied.',
        );
      } catch (error) {
        console.error(
          '[SYLPHY] Could not apply desktop CSS:',
          error,
        );
      }
    },
  );

  win.once('ready-to-show', () => {
    if (!win || win.isDestroyed()) return;

    win.show();

    console.log(
      '[SYLPHY] Desktop companion window shown.',
    );
  });

  win.on('move', scheduleSaveState);
  win.on('resize', scheduleSaveState);
  win.on('blur', () => {
    // Avoid leaving a stale drag session if focus changes mid-drag.
    // The renderer will also send move-end on pointerup.
  });

  win.on('close', (event) => {
    if (!quitting) {
      event.preventDefault();
      win?.hide();
    }
  });

  win.on('closed', () => {
    win = null;
  });

  win.webContents.setWindowOpenHandler(
    ({ url }) => {
      if (url.startsWith('https://')) {
        void shell.openExternal(url);
      }

      return { action: 'deny' };
    },
  );

  win.webContents.on(
    'will-navigate',
    (event, url) => {
      if (!url.startsWith('file://')) {
        event.preventDefault();
      }
    },
  );

  void win.loadFile(
    path.join(
      __dirname,
      '..',
      'dist',
      'index.html',
    ),
  ).catch((error) => {
    console.error(
      '[SYLPHY] Failed to load renderer:',
      error,
    );
  });

  return win;
}

app.setAppUserModelId(APP_ID);

app.whenReady().then(() => {
  // Right-button drag moves the transparent companion window.
  // The renderer sends screen coordinates; main owns window positioning.
  let moveSession = null;

  ipcMain.on('sylphy:window:move-start', (event, screenX, screenY) => {
    if (!win || win.isDestroyed() || event.sender !== win.webContents) return;
    if (!Number.isFinite(screenX) || !Number.isFinite(screenY)) return;

    const bounds = win.getBounds();
    moveSession = {
      pointerX: screenX,
      pointerY: screenY,
      windowX: bounds.x,
      windowY: bounds.y,
    };
  });

  ipcMain.on('sylphy:window:move', (event, screenX, screenY) => {
    if (!win || win.isDestroyed() || event.sender !== win.webContents || !moveSession) return;
    if (!Number.isFinite(screenX) || !Number.isFinite(screenY)) return;

    const x = Math.round(moveSession.windowX + screenX - moveSession.pointerX);
    const y = Math.round(moveSession.windowY + screenY - moveSession.pointerY);
    win.setPosition(x, y, false);
  });

  ipcMain.on('sylphy:window:move-end', (event) => {
    if (win && !win.isDestroyed() && event.sender === win.webContents) {
      moveSession = null;
      scheduleSaveState();
    }
  });
  ipcMain.handle(
    'sylphy:window:minimize',
    () => {
      win?.hide();
      return true;
    },
  );

  ipcMain.handle(
    'sylphy:window:close',
    () => {
      win?.hide();
      return true;
    },
  );

  ipcMain.handle(
    'sylphy:window:toggle-topmost',
    () => {
      if (!win || win.isDestroyed()) {
        return false;
      }

      const next = !win.isAlwaysOnTop();

      win.setAlwaysOnTop(
        next,
        'floating',
      );

      return next;
    },
  );

  ipcMain.handle(
    'sylphy:window:get-state',
    () => ({
      alwaysOnTop: Boolean(
        win?.isAlwaysOnTop(),
      ),
      bounds: win?.getBounds() ?? null,
      visible: Boolean(
        win?.isVisible(),
      ),
    }),
  );

  // Read-only Entity status.
  // Avatar never controls Entity lifecycle.
  ipcMain.handle(
    'sylphy:entity:status',
    async () => {
      try {
        const response = await fetch(
          'http://127.0.0.1:8765/status',
          {
            signal: AbortSignal.timeout(1800),
          },
        );

        if (!response.ok) {
          throw new Error(
            `Entity status HTTP ${response.status}`,
          );
        }

        const body = await response.json();

        return {
          connected: true,
          lifecycle:
            typeof body.lifecycle === 'string'
              ? body.lifecycle
              : 'unknown',
        };
      } catch (error) {
        return {
          connected: false,
          lifecycle: 'unavailable',
          error:
            error instanceof Error
              ? error.message
              : String(error),
        };
      }
    },
  );

  // Read-only Voice playback projection. Avatar receives timing/amplitude only;
  // it never starts, stops, or otherwise owns the Entity Voice Runtime.
  ipcMain.handle(
    'sylphy:avatar:presentation',
    async () => {
      try {
        const response = await fetch(
          'http://127.0.0.1:8765/avatar/presentation',
          { signal: AbortSignal.timeout(1200) },
        );
        if (!response.ok) return null;
        const body = await response.json();
        if (!body || !Number.isSafeInteger(body.revision)
          || typeof body.behavior !== 'string' || !Array.isArray(body.commands)) return null;
        return body;
      } catch {
        return null;
      }
    },
  );

  ipcMain.handle(
    'sylphy:voice:status',
    async () => {
      const offline = {
        enabled: false,
        state: 'offline',
        is_speaking: false,
        active_session_id: null,
        entity_instance_id: null,
        session_generation: 0,
        sequence: 0,
        position_seconds: 0,
        duration_seconds: 0,
        amplitude: 0,
      };
      try {
        const response = await fetch(
          'http://127.0.0.1:8765/voice/status',
          { signal: AbortSignal.timeout(1200) },
        );
        if (!response.ok) return offline;
        const body = await response.json();
        const numeric = (value, fallback = 0) =>
          Number.isFinite(value) && value >= 0 ? value : fallback;
        return {
          enabled: body.enabled === true,
          state: typeof body.state === 'string' ? body.state : 'unavailable',
          is_speaking: body.is_speaking === true,
          active_session_id: typeof body.active_session_id === 'string'
            ? body.active_session_id
            : null,
          entity_instance_id: typeof body.entity_instance_id === 'string'
            ? body.entity_instance_id
            : null,
          session_generation: Number.isSafeInteger(body.session_generation)
            && body.session_generation >= 0 ? body.session_generation : 0,
          sequence: Number.isSafeInteger(body.sequence) && body.sequence >= 0
            ? body.sequence : 0,
          position_seconds: numeric(body.position_seconds),
          duration_seconds: numeric(body.duration_seconds),
          amplitude: Math.min(numeric(body.amplitude), 1),
        };
      } catch {
        return offline;
      }
    },
  );


  // Chat: send message to Entity Conversation Runtime.
  ipcMain.handle('chat:send', async (_e, { text, sessionId }) => {
    const token = readControlToken();
    if (!token) return { accepted: false, error: 'no_token' };
    try {
      const res = await fetch('http://127.0.0.1:8765/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Sylphy-Control-Token': token },
        body: JSON.stringify({ text, session_id: sessionId }),
        signal: AbortSignal.timeout(6000),
      });
      return await res.json();
    } catch (e) { return { accepted: false, error: String(e) }; }
  });

  ipcMain.handle('chat:history', async (_e, { sessionId }) => {
    const token = readControlToken();
    if (!token) return null;
    try {
      const url = `http://127.0.0.1:8765/conversation/history?session_id=${encodeURIComponent(sessionId)}&limit=50`;
      const res = await fetch(url, {
        headers: { 'X-Sylphy-Control-Token': token },
        signal: AbortSignal.timeout(3000),
      });
      if (!res.ok) return null;
      return await res.json();
    } catch { return null; }
  });

  ipcMain.handle('chat:speak', async (_e, { text }) => {
    const token = readControlToken();
    if (!token) return { accepted: false };
    try {
      const res = await fetch('http://127.0.0.1:8765/voice/speak', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Sylphy-Control-Token': token },
        body: JSON.stringify({ text }),
        signal: AbortSignal.timeout(5000),
      });
      return await res.json();
    } catch { return { accepted: false }; }
  });
  // R7 Sanctuary: one normal desktop window contains chat and the VRM viewport.
  createChatWindow();

  const trayImage = nativeImage.createFromDataURL(
    'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/pWQAAAAASUVORK5CYII=',
  );

  tray = new Tray(trayImage);
  tray.setToolTip('SYLPHY Avatar');

  tray.setContextMenu(
    Menu.buildFromTemplate([
      {
        label: 'Open Sanctuary',
        click: () => createChatWindow(),
      },
      {
        type: 'separator',
      },
      {
        label: 'Show Sylphy',
        click: () => createChatWindow(),
      },
      {
        label: 'Hide Sylphy',
        click: () => chatWin?.hide(),
      },
      {
        type: 'separator',
      },
      {
        label: 'Always on top',
        type: 'checkbox',
        checked: false,
        click: (item) => {
          chatWin?.setAlwaysOnTop(item.checked, 'floating');
        },
      },
      {
        type: 'separator',
      },
      {
        label: 'Quit Sylphy',
        click: () => app.quit(),
      },
    ]),
  );

  globalShortcut.register(
    'CommandOrControl+Shift+S',
    () => {
      if (!chatWin || chatWin.isDestroyed()) { createChatWindow(); return; }
      if (chatWin.isVisible()) chatWin.hide(); else { chatWin.show(); chatWin.focus(); }
    },
  );

  globalShortcut.register('CommandOrControl+Shift+C', () => createChatWindow());

  globalShortcut.register(
    'CommandOrControl+Shift+Q',
    () => app.quit(),
  );

  app.on('activate', () => createChatWindow());
});

app.on('before-quit', () => {
  quitting = true;

  clearTimeout(saveTimer);
  saveState();

  globalShortcut.unregisterAll();

  if (tray) {
    tray.destroy();
    tray = null;
  }
});

app.on('window-all-closed', () => {
  if (
    process.platform !== 'darwin' &&
    quitting
  ) {
    app.quit();
  }
});
