# Phase 2.3 Visual QA + Theme Stabilization Plan

## Scope

Phase 2.3 stabilizes the Phase 2.2 Settings Center and theme system without adding subscription bridges, autonomous agents, board registry work, cloud sync, marketplace features, or new provider architecture.

## Work Plan

1. Inspect the current IDE shell, Settings Center, theme hook, model settings, and Next config.
2. Run the desktop dev stack and capture visual QA screenshots.
3. Fix Settings layout clipping and scroll containment.
4. Keep the right panel limited to AI Assistant and Device Tools.
5. Replace major hardcoded IDE shell colors with theme variables.
6. Verify theme switching, persistence across reload, and System preference handling where implemented.
7. Verify Settings -> Models keeps masked keys, provider actions, task routes, usage, and top-bar model entry.
8. Verify generation/build/external project workflow regressions.
9. Run required backend, frontend, and Electron verification commands.

## Implementation Notes

- Settings remains in the main workbench area.
- Model settings remain under Settings -> Models.
- The right panel keeps only AI Assistant and Device Tools.
- Narrow Settings layouts hide explorer and the right panel below configured viewport thresholds so the selected settings category keeps usable width.
- Theme cleanup focuses on the active ForgeX IDE shell, Settings pages, editor chrome, explorer, bottom panel, right panel, and status bar. Monaco internals remain lightly integrated.
- Next.js multiple-lockfile root inference is handled with `outputFileTracingRoot`; no lockfiles were deleted.

## Verification Targets

- `python -m compileall backend`
- `python -m pytest`
- `npm --prefix frontend run typecheck`
- `npm --prefix frontend run build`
- `npm run build:electron`
- `npm run dev:desktop`

