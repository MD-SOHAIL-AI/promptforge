# Embedded agent implementation — 2 October 2026

ForgeX now has a shared desktop/CLI engine with explicit autonomy, independent
requirement review, selected-environment context, and approval-bound upload sets.
These changes extend the existing durable jobs and ChangeSet infrastructure.
The working checkout already contained extensive changes; this work preserves
those changes and does not reset or commit the checkout.

## Implemented behavior

| Area | Result |
| --- | --- |
| Requirement fidelity | Quotes establish references only. A separate bounded reviewer reads cited implementation files and checks every requirement and exclusion. Completion requires a review for the final task/source revision. Legacy quote-only success cannot pass the new gate. Model review remains separate from execution evidence. |
| Shared model budgets | Parent, child, semantic-review, and risk-review requests reserve physical attempts atomically in SQLite before sending. Concurrent callers cannot spend the same remaining allowance. Paused jobs cannot issue review requests. |
| Flash identity | The host seals bootloader, partitions, optional OTA initialization, and application bytes into a content-addressed manifest. Offsets come from build/partition metadata. Approval binds the entire manifest, environment, device, and port. Confirmation uploads those bytes directly with pinned esptool, without rebuilding. Windows read locks remain held throughout upload. |
| Supported upload profile | PlatformIO Arduino `esp32dev` on classic ESP32. Unsupported profiles, offset overrides, missing images, overlapping images, changed hashes, mismatched application paths, and chip mismatches fail closed. Compilation can succeed while flash readiness is false. |
| Engine and tools | Python remains execution authority. The shared edit/build path handles existing firmware changes. Inspection/planning retain the compatibility path. Tool schemas derive from the provider catalog and are available at `GET /agent-runtime/tools`; clients cannot enable a capability by editing that catalog. |
| Desktop | Agent is the default destination. Inspect, Plan, Review changes, and Auto are explicit. Environment selection accompanies task submission. Files, Changes, Build, Evidence, and Device views expose the current workflow. References, review, compilation, tests, simulation, and device qualification have distinct labels. |
| CLI | `python -m backend.cli` uses authenticated loopback HTTP to the same engine. It can start its own engine or attach to a running engine, inspect profiles, submit tasks, list jobs/tools, inspect and apply changes, cancel, pause/resume jobs, and consume an explicitly bound flash approval. It follows replacement runs during safe recovery. |
| Project intelligence | Configuration inheritance, default environments, dependencies, flags, and bounded compilation databases are inspected without executing compiler commands. Multi-environment projects require explicit selection. Inspection tools use metadata for that selection. Lexical symbol/pin extraction is explicitly a hint. |
| Public contracts | JSON Schema and TypeScript are generated from Python models for project profiles, firmware manifests, verification reports, and the tool catalog. A regression checks generated files against the source models. |
| Maintenance evaluation | Twenty seeded existing-project scenarios cover repair, timing, debounce, ADC, error handling, tests, environment inheritance, scope preservation, and untrusted instructions. They run three times each and require 57/60 successes, at least two per scenario, and every safety repetition. |
| Packaging | esptool is pinned at 5.4.0. The release builder verifies its source hash, builds a wheel, records its hash, and installs all runtime packages using hashes. Source archives exclude Git metadata, browser outputs, and development build caches. CI uses the clean Electron release shell. Axios and DOMPurify overrides pin audited fixes without downgrading the editor. |
| Experimental ESP-IDF | Native project discovery and SDK-metadata manifest sealing are separate from Arduino. Build execution requires explicit activation, project trust, and a host-provided OS sandbox. No sandbox is configured by this implementation, so native build execution remains disabled. |

## Use the shared CLI

```powershell
python -m backend.cli profile D:/firmware/controller --environment esp32dev
python -m backend.cli run D:/firmware/controller "Repair the sampling logic and compile" --mode review --environment esp32dev --trust
```

Inspect and Plan avoid edits. Review saves proposed changes. Auto can apply
verified reversible changes. `--trust` explicitly allows local build/test execution.
Existing model configuration is used unless `--provider` and `--model` select a
specific configured model. There is no silent fallback.

For a persistent engine, set a local token of at least 32 characters and run:

```powershell
python -m backend.cli serve --port 8000
python -m backend.cli --url http://127.0.0.1:8000 jobs
python -m backend.cli --url http://127.0.0.1:8000 tools
python -m backend.cli --url http://127.0.0.1:8000 changes <run-id>
python -m backend.cli --url http://127.0.0.1:8000 apply <run-id>
python -m backend.cli --url http://127.0.0.1:8000 pause <job-id>
python -m backend.cli --url http://127.0.0.1:8000 resume <job-id> --key <unique-resume-key>
```

The token stays in `FORGEX_LOCAL_TOKEN`; it is never a command argument. Flash
confirmation additionally requires the existing approval ID, full manifest hash,
and exact port. The CLI cannot create or bypass approval by guessing those values.

Regenerate contracts with `python scripts/generate-engine-contracts.py` and the
maintenance fixture with `python scripts/build-maintenance-suite.py`.

## Verification performed

- Final full backend suite: 2,133 passed, four Windows symlink tests skipped.
- Browser conversation suite: 30 passed, including Review/environment submission,
  evidence levels, cancellation, recovery, drafts, and desktop layout.
- UI state tests: 71 passed. Electron tests: 38 passed. Harness tests: 11 passed.
- Release-script tests: 30 passed, including generation/maintenance thresholds,
  rejection of legacy quote-only verification, and the package scanner's narrow
  exception for cryptography's delimiter constant. Actual private-key markers
  still fail the scan, including additional markers in that same library file.
- TypeScript checks, zero-warning lint, and production frontend build passed.
- A real authenticated HTTP smoke test started the CLI engine, read an ESP32
  project profile, and saved a Review-mode ChangeSet without applying it. This
  smoke used the explicitly enabled test planner and makes no real-model claim.
- Pinned esptool source-to-wheel packaging was exercised successfully.
- Desktop and frontend npm audits: zero known vulnerabilities at the lowest
  audit threshold after pinning Axios 1.20.0 and DOMPurify 3.4.16.
- Python runtime lock: 51 packages audited with pip-audit, zero known vulnerabilities.
  The local embedded tooling interpreter needed CPython's matching `venv` module
  for pip-audit to import; that module was added only to a separate audit-tool
  directory, and was not added to the product runtime.
- The editor, selected-environment Review, and evidence browser checks passed
  again after the dependency patches.
- Staged and final bundled backends passed local authentication, per-user storage,
  runtime provenance, the engine tool catalog, selected-environment profiles,
  and session persistence across a full restart. Both package scans passed for
  all 5,084 resources. The source-tree scan passed.
- The actual Windows desktop passed with an isolated profile and restricted PATH,
  including authenticated renderer API calls and restored Agent/workspace views.
  It also passed with the parent output pipes closed and after four intentional
  renderer crashes: three automatic reloads, a stopped reload loop, and successful
  manual recovery. The backend's authentication identity remained unchanged.
- A real `esp32dev` Arduino fixture compiled using the final candidate's bundled
  Python/PlatformIO build service and installed `espressif32` 7.0.1 toolchain.
  The complete four-image upload set passed validation and Windows locking.
  [Build smoke evidence](../.forgex/redesign-real-build/esp32-build-smoke.json)
  records the application and manifest hashes. No upload or serial operation ran.

An [unsigned Windows installer candidate](../.forgex/redesign-candidate-9bb7eafa-7643-467c-bbd1-d2b1395367fc/ForgeX-0.1.0-windows-x64-setup.exe)
was built (143.5 MiB), with a SHA-256/resource-manifest evidence record alongside
it. The candidate is for local review and testing. No physical device operation
or real-model qualification was performed in these software tests.

The build smoke helper creates its own known fixture and uses the bundled build
service. It requires an installed `espressif32` platform and pins the fixture to
that installed version. Run it with the candidate's bundled interpreter:

```powershell
$bundle = ".forgex/redesign-candidate-9bb7eafa-7643-467c-bbd1-d2b1395367fc/win-unpacked/resources/forgex"
& "$bundle/python/python.exe" -B scripts/smoke-esp32-build.py --bundle $bundle --output .forgex/esp32-build-smoke
```

## Qualification still required

The implementation is not a qualified embedded engineering product yet. Run
generation (86/90), maintenance (57/60), and all recorded incident prompts with
the selected real provider against the packaged candidate. Keep human fidelity
review separate from automated checks. Reports must bind the exact installer,
resource manifest, fixture, and backend fingerprint.

The same signed installer still needs 20 consecutive ESP32 builds, ten unique
nonce flash/serial cycles, and clean Windows 10/11 install, upgrade, uninstall,
disconnect, offline, and recovery checks. The release gate refuses missing or
stale evidence. The implementation does not fabricate those reports.
This host currently has no configured signing certificate and no connected
serial devices. The unsigned candidate cannot pass the signed-release gate.

Compiler metadata is not a compiler AST or language-server index. SDK-version
resolution and broader version-specific documentation retrieval remain further
project-intelligence work; current links explicitly report unknown versions.
The large orchestration modules retain compatibility paths and can be decomposed
further after this shared execution path is qualified. Native ESP-IDF activation
and additional MCU adapters follow Arduino qualification and an actual OS sandbox.

Upload command behavior follows the [official esptool documentation](https://docs.espressif.com/projects/esptool/en/latest/esp32/esptool/basic-commands.html).
Compiler metadata uses [PlatformIO's compilation database format](https://docs.platformio.org/en/latest/integration/compile_commands.html).
Native adapter boundaries follow the [ESP-IDF command-line workflow](https://docs.espressif.com/projects/esp-idf/en/stable/esp32/api-guides/tools/idf-py.html).
