# R4 Humanoid hierarchy metadata hotfix

## Root cause
`HumanoidRig.report` used `node.parent?.name || null`. This converts an existing parent node whose name is the empty string into `null`. The test fixture expects the actual parent name (`''`) to be preserved.

## Fix
Use nullish coalescing: `node.parent?.name ?? null`. A missing parent remains `null`; an existing parent with an empty name remains `''`.

## Apply
Extract this patch into the repository root, preserving `avatar/src/humanoid-rig.ts`, and overwrite the file.

Run from `avatar/`:
```powershell
npm test
npm run build
```
