# R4 Clip Count UI Fix

## Root cause
`avatar/src/main.ts` queries `#clip-count` and writes its `textContent` after a VRM is loaded, but `avatar/index.html` did not define an element with that ID. This caused `Cannot set properties of null (setting 'textContent')` during model loading.

## Fix
Added an `Available clips` metric with `<strong id="clip-count">0</strong>` to the Animation Clip Registry section. All IDs queried by `main.ts` are now present in `index.html`.

## Apply
Extract into the repository root and overwrite `avatar/index.html`.

Then restart Vite (`Ctrl+C`, `npm run dev`) and hard-refresh the browser (`Ctrl+F5`).
