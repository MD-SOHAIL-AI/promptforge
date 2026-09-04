# Phase 2.5.8.14 — AGY Assisted Scratch Generation Results

## Implementation

ForgeX now has an experimental `agy_scratch_runner` provider and a guarded assisted-generation service. The only product template is `esp32-platformio-blink`. Product execution requires `FORGEX_ENABLE_AGY_ASSISTED_RUNNER=1` and an explicit **Run AGY and Create Review** click. Real QA additionally requires `--confirm-real-agy`.

The runner uses a unique marked direct child of `C:\forgex-agy-runs`, direct argv, `shell:false`, a bounded timeout, and memory-only process output. It checks one precomputed expected AGY scratch folder and delegates all validation, copying, active-workspace verification, and review creation to the Phase 2.5.8.13 import service.

Project type detection supports PlatformIO, Arduino, ESP-IDF, and MicroPython. Assisted reviews include the fixed template, expected-source identity, relative file tree, total bytes, project type, risk warning, and unchanged-workspace evidence. No automatic apply, build, or flash path was added.

## Verification status

The guarded real validation required two attempts. The first found AGY `1.0.15` but returned `AGY_ASSISTED_PROVIDER_ERROR` before creating the exact folder. Comparison with ForgeX's existing vetted AGY environment showed that the new runner omitted safe process-compatibility variables. After aligning that allowlist—without adding secrets, API keys, or credential paths—the one comparison run passed.

| Field | Final result |
| --- | --- |
| AGY assisted runner added | yes |
| AGY direct editor enabled | no |
| Required flags | `FORGEX_ENABLE_AGY_SCRATCH_IMPORT`, `FORGEX_ENABLE_AGY_ASSISTED_RUNNER` |
| Safe templates | `esp32-platformio-blink` only |
| Invocation cwd | unique marked child of `C:\forgex-agy-runs` |
| Exact expected folder rule | yes |
| Full scratch scan | no |
| Newest-folder selection | no |
| Manual import fallback | preserved |
| Real AGY attempts | 2 |
| Final classification | `AGY_ASSISTED_IMPORT_PASS` |
| AGY version | `1.0.15` |
| Expected folder found | yes |
| File count | 3 |
| Total bytes | 1,079 |
| Project type | PlatformIO |
| Review created | yes |
| Review ID | `bridge-review-673502cee5e44c9e925bdda41060df31` |
| Active workspace unchanged | yes |
| Auto-apply / build / flash | no / no / no |

The successful invocation workspace retained only the ForgeX marker. The persistent review identifies `agy_scratch_runner`, the fixed template, `expected_agy_scratch_folder`, three created files, PlatformIO project type, and one manual-review warning. The status and review records contain no runtime instruction or raw process output.

## Regression and automated verification

| Verification | Result |
| --- | --- |
| Required focused selector | 88 passed, 2 skipped, 1,748 deselected |
| Fake-assisted/import/API group | 74 passed, 2 skipped |
| Assisted QA gate tests | 3 passed |
| Final full backend | 1,827 passed, 12 skipped |
| Backend compileall | passed |
| Safety scan | 236 passed |
| Frontend typecheck | passed |
| Frontend production build | passed; 4 static pages |
| Electron build | passed |
| Electron tests | 10 passed |
| Manual import regression | `AGY_SCRATCH_IMPORT_PASS`; 8 files, 10,614 bytes, review created |

Phase 2.5.8.14 meets the success definition. Recommended next phase: **Phase 2.5.8.15 — AGY Import Apply/Preflight Hardening**.
