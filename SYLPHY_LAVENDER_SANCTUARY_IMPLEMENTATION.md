# SYLPHY Lavender Sanctuary — UI implementation slice

## What changed

- The primary Electron window is now a normal, framed, resizable desktop window titled `SYLPHY · Lavender Sanctuary`.
- The startup path opens one unified Sanctuary window rather than starting the transparent avatar overlay and then opening a detached chat window.
- `desktop/chat.html` now uses the selected asymmetric layout: navigation rail + conversation panel on the left, VRM viewport on the right.
- Visual palette: lavender, ivory, muted violet and champagne-gold accents; soft glass-like panels.
- The right-side viewport embeds the existing Vite VRM renderer at `../dist/index.html?embed=avatar`.
- Embedded mode hides preview-only controls and automatically loads the bundled `Sylphy.vrm` asset.
- The chat preload exposes only the existing chat actions and a minimize action. The embedded VRM iframe is intentionally isolated; it loads the bundled model and renders its idle presentation, while Entity presentation polling and lip-sync inside the iframe remain a Windows integration follow-up.
- No Python runtime, database, Memory, Cognition, Voice model, Electron package, lockfile or `node_modules` was changed.

## Files in this patch

- `avatar/desktop/chat.html`
- `avatar/desktop/chat-preload.cjs`
- `avatar/desktop/main.cjs`
- `avatar/src/main.ts`
- `avatar/src/style.css`

## Apply on Windows

1. Close Sylphy Desktop.
2. Extract this patch into the `sylphy_work` repository root and allow overwriting only the listed files.
3. Do **not** run `npm ci` and do not delete `node_modules`.
4. From `avatar`, run `npm test` and `npm run desktop:build`.
5. Run `npm run desktop`.

The first launch should open one normal Sanctuary window. The right panel loads the bundled VRM from `avatar/public/Sylphy.vrm` via the existing Vite build output. The existing detached overlay code remains in the source but is no longer the default startup path; it can be removed after Windows acceptance.

## Verification performed in the patch workspace

- `node --check avatar/desktop/main.cjs` — passed.
- `node --check avatar/desktop/chat-preload.cjs` — passed.
- Python HTML parser — both `chat.html` and `index.html` parsed successfully.
- Full Vitest / TypeScript / Electron runtime verification was not run here because this extracted review workspace does not include `node_modules` or an Electron runtime. Run those checks on the user's existing Windows installation without reinstalling dependencies.

## Known follow-up

The navigation rail is a visual shell for future Memory, Voice, Activity and Settings pages. Only Conversation, Speak, connection status and the embedded VRM viewport are wired in this slice. The VRM frame is intentionally isolated from Node/Electron APIs. Confirm model loading on Windows; wiring read-only presentation/voice updates into the iframe is a follow-up acceptance task.
