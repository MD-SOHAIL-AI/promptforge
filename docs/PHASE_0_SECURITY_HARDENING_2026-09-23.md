# Phase 0 security hardening — 2026-09-23

This implements the Phase 0 items in `REPOSITORY_REVIEW_2026-09-23.md`.

| Review item | Change |
| --- | --- |
| EXE-1 | `/execute` accepts a workspace path only when it resolves to a managed or imported project. A supplied `project_id` and `workspace_root` must resolve to the same project. The schema requires an existing absolute directory; execution context construction checks that the project path still matches the active workspace. Build requests carry the approved canonical root into the builder, which rejects a different resolved root. PlatformIO config symlinks are rejected. |
| AF-F1 | Build policy rejects URL, VCS, and local path values for `platform`, `board`, and `lib_deps`; registry identifiers and version constraints remain supported. |
| TERM-1 / XM-2 | Non-health HTTP requests and WebSockets require a local token. Host must use the configured backend loopback port; Origin must use the configured frontend or backend loopback port. Tokenless standalone backend serves `/health` only. Health also validates Host and Origin. |
| XM-1 | Legacy direct `POST /flash` returns `FLASH_APPROVAL_REQUIRED` before building or touching hardware. The workspace action points users to the Forge task's verified-build and one-time confirmation flow. |
| EF-F01 | Packaged frontend starts refuse an occupied port. The spawned Next.js server proves possession of a per-launch secret through a fresh HMAC challenge before the desktop loads its URL. Packaged URL overrides no longer bypass this check. |
| OR-F9 | Transaction setup releases the store lock and closes any connection on failure; nested transactions fail immediately. |

Verification: `npm run verify:beta` passed all six checks on the final code state, including 1,708 Python tests (4 host-dependent symlink skips), 9 harness tests, 64 frontend state tests, 30 Electron tests, frontend typecheck, and zero-warning lint. `npm --prefix frontend run build` included the dynamic `/desktop-identity` route; a live challenge against the built server returned a valid proof.

Operational changes: `npm run dev:desktop` provisions the token and cookie. Standalone backend operators must set `FORGEX_LOCAL_TOKEN` to at least 32 characters for API access. The legacy direct flash UI action no longer starts a flash; use a Forge task to prepare and confirm it.
