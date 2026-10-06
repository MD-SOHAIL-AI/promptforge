# ForgeX 0.2.0 UI redesign candidate

## Scope and behavior

ForgeX now uses a shared graphite design system with bundled Manrope and
JetBrains Mono fonts, restrained cyan accents, selective glass surfaces,
consistent navigation and responsive controls. The conversation stays central;
the resizable workspace exposes Files, Changes, Build, Evidence, Device and
Terminal. Projects have functional search and recent-project limits. Settings
have nine categories, search that focuses the matching control, category reset,
save status and visible recovery errors.

The live execution journal supports Summary, Normal and Verbose detail. Commands,
output, edits and nested agent activity appear in one timeline. Finished durations
freeze; stopped or interrupted work retains its actual outcome. It does not turn
unfinished steps into successful steps. History is paged into at most 80 visible
groups, and the live transport retains its latest 2,000 events. The browser
regression replays 10,000 SSE events through the real frontend reducer.

Appearance now copies a selected local image into protected application storage,
validates image headers and decoded dimensions, persists its identity with its
name, and renders the same image/dim layers in the workspace and preview. PNG,
JPEG and WebP are supported, with limits of 8,192 pixels per axis and 32 megapixels.
The original file is preserved. Replacement and removal clear obsolete copied
assets after the preference write succeeds. Failed imports/writes roll back;
missing images produce a recoverable error. Transparency controls the surfaces
over the image inside ForgeX.

Settings writes are serialized and coalesced with immediate visual previews and
rollback to saved values on failure. Backend persistence is locked and atomic.
Numeric and list inputs accept complete edits before committing. Editor, terminal,
workspace restoration, explorer filters, device polling, build environment,
startup destination, motion, contrast and density preferences now reach their
corresponding runtime controls. Legacy hardware defaults that never controlled a
project are hidden; project environments remain authoritative. Hardware approval
and key masking protections remain enforced.

The Monaco editor runtime, language assets and workers are now generated from the
locked dependency into local public assets on frontend dev/build. Editing no
longer requires a CDN. The editor browser regression blocks external requests
and verifies local loader requests and visible source text.

The existing recovery protections remain: provider failures do not silently select
another model, Stop retains the recovery draft, Discard is separate, changes require
verified build evidence, and physical hardware actions require approval.

## Design references

The implementation uses session navigation, a compact live journal and workspace
tools inspired by the primary product references reviewed for the plan:
[Codex app](https://openai.com/index/introducing-the-codex-app/),
[Claude Code desktop redesign](https://claude.com/blog/claude-code-desktop-redesign),
and [Grok documentation](https://docs.x.ai/grok/overview).

## Dependency remediation

The release audits exposed two advisories published before this candidate:

- [GHSA-ch52-4w7c-c8xp](https://github.com/advisories/GHSA-ch52-4w7c-c8xp):
  the Electron downloader is pinned to `@electron/get` 5.1.0, removing the old
  `got` / `cacheable-request` / `http-cache-semantics` dependency chain. A regression
  loads the actual Electron Builder downloader API.
- [GHSA-vfj7-8cjw-p6xm](https://github.com/advisories/GHSA-vfj7-8cjw-p6xm):
  upstream braces 3.0.3 has no patched release. The source-controlled MIT-licensed
  `@forgex/braces` fork imposes a non-disableable parser and AST traversal depth
  limit. All transitive consumers use the identified fork. Tests exercise normal
  globbing, nested patterns below the old length limit, external ASTs and cycles.
  This is a maintained local patch, not an upstream patched version. Its README
  records provenance and the replacement policy.

Audits remain release gates; no advisory is ignored and no severity threshold is
lowered. The local fork must be maintained until a verified upstream fix replaces
it. Font and vendored library licenses are included with their source assets.

Installer assembly uses the installed, version-checked Electron distribution
through Electron Builder's supported unpacked `electronDist` input. This avoids
the Windows directory rename that repeatedly failed during ZIP extraction.
The exact Electron pin and signing requirements remain enforced.

## Verification and artifacts

Installer: `.forgex/installers/ForgeX-0.2.0-windows-x64-setup.exe`
(155,373,782 bytes), with build evidence beside it as `.exe.evidence.json`.

SHA-256: `253661f56104b9b2e6b76caa5dcd37675e0116f78d6c6befd3cdea6fda3b97fa`.

The unpacked candidate is in
`.forgex/installers/redesign-0.2.0/win-unpacked/ForgeX.exe`.
The previous 0.1.1 installer remains at
`.forgex/installers/ForgeX-0.1.1-windows-x64-setup.exe`; its retained SHA-256 is
`b2837e36c21e5b1b7dc4c20f436242b335ba231f76ea908f166b129d49e242e5`.

| Check | Result | Evidence |
| --- | --- | --- |
| Backend suite | 2,151 passed; four Windows symlink cases skipped | `.forgex/redesign-backend-suite.log` |
| Final settings persistence checks | 21 passed | `.forgex/redesign-settings-tests.log` |
| Frontend state | 75 passed | `.forgex/redesign-ui-state.log` |
| Electron | 41 passed | `.forgex/redesign-electron.log` |
| Harness | 11 passed | `.forgex/redesign-harness.log` |
| Release scripts and dependency security regressions | 35 passed | `.forgex/redesign-release-scripts.log` |
| Browser suite with final local editor assets | 42 passed | `.forgex/redesign-final-browser.log` |
| Typecheck, zero-warning lint and standalone production build | Passed | `.forgex/redesign-typecheck.log`, `.forgex/redesign-lint.log`, `.forgex/redesign-production-build.log` |
| Clean frontend dependency install and software inventory | Passed | `.forgex/redesign-frontend-clean-install.log`, `.forgex/redesign-frontend-sbom.json` |
| Desktop/frontend npm audits | Zero reported vulnerabilities | `.forgex/redesign-desktop-audit.log`, `.forgex/redesign-frontend-audit.log` |
| Locked Python runtime audit | No known vulnerabilities found | `.forgex/redesign-python-audit.log` |
| Staged and final packaged content scans | Passed; 5,246 runtime files | `.forgex/redesign-package.log` |
| Bundled backend startup, authentication and persistence | Passed in staged and final packages | `.forgex/redesign-package.log` |
| Stop/outage/restart/Resume/Discard recovery | Passed in staged and final packages | `.forgex/redesign-package-smoke/final/live-log-reliability-smoke.json` |
| Native desktop image protocol, decode, preview, opacity restoration and removal | Passed with an isolated profile and restricted PATH | `.forgex/redesign-package.log` |
| Native startup with parent output pipes closed | Passed | `.forgex/redesign-package.log` |
| Renderer recovery | Three crashes reload; fourth stops; manual reload restores the app with unchanged backend identity | `.forgex/redesign-package.log` |
| Bundled ESP32 compile and complete sealed upload set | Passed with PlatformIO 6.2.0 and Espressif32 7.0.1 | `.forgex/redesign-esp32-build/esp32-build-smoke.json` |

The backend suite reports two dependency deprecation warnings. Browser tests use
mocked API/SSE transport and an Edge renderer; the packaged smoke uses real
Electron, backend, asset IPC/protocol and persistent settings in temporary profiles.
Provider outage recovery deliberately injects provider outcomes. The ESP32 check
compiles a host-owned fixture and verifies the bootloader, partition table, OTA
data and application upload images. No physical device operation is performed.

Reviewed screenshots:

- `test-results/redesign-packaged.png`: actual packaged Appearance page with a
  native local image and restored opacity.
- `test-results/redesign-agent.png`: compact live journal browser fixture.
- `test-results/redesign-agent-narrow.png`: narrow conversation and reachable
  composer actions.
- `test-results/redesign-editor.png`: source text rendered by the local Monaco
  runtime while external asset requests are blocked.
- `test-results/redesign-narrow-settings.png`, `redesign-projects.png`, and
  `redesign-tool-*.png`: settings, projects and workspace tools.

Screenshots and check logs are local review artifacts. The previous installer is
preserved; this work does not install over the user's existing application.

## Release qualification

This is an unsigned test candidate. Distribution still requires publisher signing,
real provider prompt/quality/maintenance qualification, physical ESP32 flash/serial
signoff, and clean Windows 10/11 installation, upgrade and recovery evidence for
this exact installer. Automated UI fixtures and a software ESP32 compile do not
satisfy those external release gates. The release-evidence verifier continues to
reject an unsigned candidate or mismatched qualification evidence.
