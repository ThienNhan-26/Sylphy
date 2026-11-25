# SYLPHY Adaptive Sanctuary + Dark Mode patch

This patch updates only the Electron Sanctuary presentation and its window-state tests.

## Included files
- `avatar/desktop/chat.html`: Lavender light/dark/system themes; responsive companion-only layout at <=560px width or <=300px height; double-click avatar / click Expand to restore Sanctuary.
- `avatar/desktop/main.cjs`: normal framed resizable Sanctuary window, minimum 160x160, persists geometry, restores previous expanded size through the expand action.
- `avatar/desktop/chat-preload.cjs`: exposes the narrow `expand()` action through contextBridge.
- `avatar/desktop/window-state.cjs`: version 6 state contract, 1180x780 default, 160x160 minimum, safe upper bounds.
- `avatar/tests/desktop-window-state.test.ts`: tests the new window-state contract.

## Apply
Close SYLPHY Desktop. Extract this ZIP into the `sylphy_work` project root and allow overwrite. Then from `avatar` run:

```powershell
npm test
npm run desktop:build
npm run desktop
```

Do not run `npm ci`. No Electron binaries, `node_modules`, Python runtime, Memory, Conversation or Voice runtime files are included.

## Behavior
- Theme button cycles Light -> Dark -> System and persists in localStorage.
- When the window is resized to <=560px wide OR <=300px high, CSS hides chat and shows only the VRM companion.
- The compact mode is the same window and same renderer; it does not create another Entity session.
- Click `Mở rộng` or double-click the avatar panel to restore the last expanded size (at least 1180x780, constrained to the current display work area).
- Free resize minimum is 160x160 CSS/device-independent pixels (Electron window units).
