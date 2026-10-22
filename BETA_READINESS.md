# ForgeX Production-Beta Readiness

This file is the current release contract. Historical validation documents describe earlier milestones and do not override it.

## Certified beta boundary

- **Host:** Windows desktop on a clean Windows 10 or Windows 11 x64 installation.
- **Hardware:** ESP32 DevKit V1-compatible board, PlatformIO `espressif32`, Arduino framework, and the `esp32dev` environment.
- **Agent runtime:** Provider-neutral API/local model routing through ForgeX-owned tool contracts. Models may plan and request tools; ForgeX remains the sole executor of file, build, serial, and flash operations.
- **Safety:** Applying a ChangeSet and flashing firmware remain explicit ForgeX-controlled actions. Flash approval is one-time and bound to the run, board, port, environment, and verified artifact hash.

The following remain experimental and are not release-blocking: macOS/Linux hosts, Arduino Uno, STM32, ESP32-S3/C3 variants, Wokwi, and any non-`esp32dev` PlatformIO environment.

## Automated gate

Install the pinned dependencies, then run the single non-destructive software gate:

```powershell
python -m pip install -r requirements.txt
npm ci
npm ci --prefix frontend
npm run test:release-scripts
npm run verify:beta
npm run build:frontend
```

`verify:beta` stops on the first failure and covers the Python suite, frontend state suite, Electron build/tests, frontend typecheck, and zero-warning lint. It does not delete caches, workspaces, or user data. The Windows CI workflow runs the same gate plus production build, dependency audits, and source-archive inspection.

Before distributing a staging directory or ZIP, scan it explicitly:

```powershell
npm run scan:release -- path\to\forgex-release.zip
```

The scanner fails closed for malformed/unsupported ZIPs and rejects local environment files, virtual environments, dependency trees, `.next`/`dist`, caches, runtime workspace data, PlatformIO/firmware outputs, symbolic links, unsafe archive paths, and nested review ZIPs. Empty workspace `.gitkeep` placeholders and `.env.example`/`.env.sample`/`.env.template` are allowed.

## Release sign-off

A green software gate is necessary but does not certify hardware. Record all of the following for the exact release candidate:

- 20 consecutive clean `esp32dev` builds.
- 10 consecutive flash/reset/serial verification cycles on the reference board, with the emitted run nonce matched before success is reported.
- Cancellation and crash/restart checks showing no mutation after terminal state and no reusable approval.
- Installer first-launch, upgrade-with-state, and uninstall checks on a clean Windows machine.
- No unresolved critical/high dependency vulnerability unless the release record contains an owner-approved exception.

Use the hardware sign-off runner to produce the required build/flash/serial evidence report:

```powershell
npm run signoff:esp32 -- --project <path-to-platformio-project> --port COM7 --report hardware-signoff-reports/esp32dev-signoff.json
```

The firmware used for the flash/serial cycles must print the compile-time nonce macro `FORGEX_HW_SIGNOFF_NONCE` on boot. The runner injects a unique nonce per cycle with PlatformIO `--project-option build_flags=...`, uploads to the selected port, monitors serial at 115200 baud by default, and fails the sign-off unless the matching nonce is observed.

Do not label a build production-beta ready until the Windows CI gate and this hardware/installer sign-off both pass.
