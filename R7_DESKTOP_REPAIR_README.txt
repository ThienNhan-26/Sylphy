SYLPHY R7 — Desktop Embodiment Repair
=====================================

Scope
-----
This patch repairs the existing Electron/VRM desktop presentation. It does not
replace Electron, reinstall npm dependencies, change the Entity daemon, or
modify node_modules.

Findings from the supplied source archive
------------------------------------------
1. `desktop/main.cjs` restored any saved width/height from `window-state.json`
   and only clamped them to the monitor work area. Incrementing the layout
   version refreshed position but did not invalidate old preview dimensions.
   A previous large preview window could therefore return as a desktop overlay.
2. `desktop/main.cjs` contained three copies of the chat IPC registration block
   embedded inside tray/shortcut/activate callbacks. The handlers were already
   registered at startup; those copies could attempt duplicate IPC handler
   registration after a UI action.
3. `desktop/chat.html` rendered a user message optimistically without an ID,
   then rendered the same persisted message again when the history poll returned
   its server message ID.
4. The VRM camera used the wide-preview framing ratio even for a narrow portrait
   companion window.

Changes
-------
- New `desktop/window-state.cjs`: versioned geometry normalization; old state
  versions reset to a 360x520 compact default; current geometry is capped at
  420x640.
- `desktop/main.cjs`: uses the state normalizer, sets BrowserWindow maximum
  dimensions, and registers each chat IPC handler exactly once. Tray/shortcut
  restore paths now call `createWindow()` if the companion window is absent.
- `desktop/chat.html`: reconciles optimistic user bubbles with persisted
  history IDs instead of showing a second bubble.
- `src/vrm-viewer.ts`: uses a portrait-specific framing ratio for the compact
  desktop companion while retaining the existing wide browser-preview framing.
- `tests/desktop-window-state.test.ts`: regression tests for old-state reset,
  bounded current-state restore, oversize clamping, and malformed dimensions.

Apply on Windows
----------------
1. Close Sylphy Desktop only. Do not stop the Entity daemon unless it is
   convenient; this patch changes only the Avatar desktop shell/renderer.
2. Extract this ZIP into the repository root (`sylphy_work`), preserving paths
   and allowing overwrite.
3. Do NOT run `npm ci`, `npm install`, or delete `node_modules`.
4. In PowerShell:
     cd avatar
     npm test
     npm run desktop:build
     npm run desktop
5. The first launch after this patch intentionally ignores old window geometry
   because the state version changed. Sylphy should open as a compact
   360x520 transparent companion near the bottom-right. New geometry is saved
   under state version 3. User resizing is limited to 420x640.

Acceptance checks
-----------------
- The avatar overlay is compact and fully visible on the desktop.
- Hiding the chat window does not resize or enlarge the avatar overlay.
- Tray "Show Sylphy" and Ctrl+Shift+S can restore a hidden companion.
- Send one message once: exactly one user bubble appears, including after the
  history poll; the assistant response appears once.
- Wide browser preview remains framed as before.
- Existing Electron reports `v37.10.3`; no Electron reinstall is part of this patch.

Verification performed while preparing this patch
--------------------------------------------------
- Node syntax checks passed for main.cjs, preload.cjs, chat-preload.cjs,
  window-state.cjs, and the inline chat renderer script.
- Four Node assertions passed for window-state migration/clamping.
- Vitest/build and interactive Windows display acceptance must be run on the
  user's existing Windows installation; they were not run in this environment.
