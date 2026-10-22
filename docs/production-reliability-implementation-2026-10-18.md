# ForgeX production reliability implementation

Status: reliability changes implemented and locally validated. **The production acceptance plan is not complete; release remains blocked.** A passing connection check or local regression suite does not qualify a model to complete projects.

## Implemented behavior

- Both workspace strategies use a versioned generation controller. Structured milestones retain stable keys, file paths, requirement IDs and dependency ordering. Legacy plans remain readable. Saved session approval hashes now bind this metadata, and restart preserves it.
- Actual file changes and fresh execution evidence drive progress. Reads, plan updates, failed edits and unchanged writes cannot keep generation alive indefinitely. Recovery warns at two stalled turns, reorients at three and stops at five. Interrupted tasks retain those counters and their cumulative budgets.
- Source changes reopen affected milestones and dependent work. Saved build/review evidence cannot establish completion for a changed source or task revision. Unknown controller versions block execution while leaving saved drafts inspectable.
- Full compiler and linker output is parsed before the display tail is trimmed. Undefined symbols, declarations, implementations and callers form bounded repair context. These matches are advisory; the compiler, linker and independent requirement/completion reviews remain authoritative. No empty method stubs or deleted requirements count as a fix.
- Configured PlatformIO source/include directories and `.cc`/`.cxx` files are supported. Declaration checks also inspect differently named implementations and header definitions. Dependency resolution returns verified versions and compatibility metadata with bounded, cached registry probes.
- Registry `show` lookups no longer reject an installation range merely because it is not an exact version. The host uses PlatformIO's installed SemVer rules, selects a matching published release and verifies that exact release and its environment compatibility. Missing versions and registry/network failures remain failures. Project declarations are preserved.
- Resolving different verified packages counts as configuration progress once per canonical package ID, with a durable limit of 32. Cache hits, query aliases, timestamps and unverified candidates cannot reset the stalled-turn guard or establish a build/review pass.
- Repeated exact edit failures retain current source context. During bounded recovery, the controller directs the model to another already authorized edit method instead of repeating an unmatched replacement. No tool argument is silently rewritten, and no extra budget is granted.
- Build-execution wording such as “run the local build” requires a successful host build for the current source. Descriptions of build commands still require documentation. Saved evidence contracts migrate to version 3 while retaining requirement IDs and invalidating obsolete review revisions.
- Accepted citations and independent review are separate phases. Feedback now directs a ready generator to finish without resubmitting unchanged evidence. If it repeats accepted citations, both workspace strategies hand the current task to the existing independent review gate. This requires matching task/source revisions and all requested execution evidence; failed reviews still block completion. Inspection-only results retain their requirement report.
- Embedded Python disables bytecode writes before importing packages, including unflagged child interpreters launched by PlatformIO. A real packaged compile exposed otherwise unmanifested cache files inside release resources; the startup configuration prevents that mutation.
- Workflow folder names retain the complete random ID with fewer redundant characters. The first live replay exposed a 261-character dependency-file path that this Windows toolchain could not create; the subsequent short-path replay reached linking.
- Saved-task controls display unfinished files, unresolved methods, remaining source-evidence IDs and the next action. Independent review remains a separate verification phase. Model settings distinguish endpoint connectivity from task qualification. Qualification defaults to **untested**, independently of catalog price or account tier.
- Review work inherits the task's explicitly selected model and shared budget. Only an explicit review-role binding may select another model; unrelated application/session main defaults cannot replace the task model.

## Release gates

`verify-release-evidence.mjs` now requires a reliability bundle in addition to existing signed-installer, hardware and clean-Windows evidence. It rejects missing evidence rather than accepting a partial matrix.

For each explicitly selected free and paid model, using its actual configured endpoint:

1. Generation: 90 attempts, at least 86 successes; at least two of three for every case; all historical and safety-critical cases pass.
2. Maintenance: 60 attempts, at least 57 successes; the same per-case and critical-case rules.
3. Target smoke tests: three successful generation/follow-up pairs for each of nine board/framework combinations: ESP32, C3 and S3 with Arduino/ESP-IDF; Uno with Arduino; STM32 Blue Pill with Arduino/STM32Cube. These smoke tests do not replace the full quality suites.
4. Original Smart Monitor incident: three fresh completions on `nvidia_nim / nvidia/nemotron-3-super-120b-a12b`, with the exact retained request and standard limits: 900 seconds, 100 tools, 120 requests, 245,760 output tokens and 8 MB cumulative input. Current firmware bytes, matching source/build/review revisions, both independent review gates and responsive backend health are required.

Reports must bind the exact installer, resource manifest, backend fingerprint, fixture, model, endpoint and profile. Reused run IDs, substituted model reports, increased limits, stale reviews, incomplete target coverage and a source-only incident report cannot satisfy the gate. Free/paid access requires explicit account evidence; catalog pricing is insufficient.

The existing signed-installer, clean Windows 10/11, 20-build and 10 nonce-verified hardware-cycle requirements remain enforced. No flashing was performed by this implementation task.

## Running qualification

Use a copy of `docs/reliability-qualification.example.json`. Fill explicit provider/model IDs, endpoint hashes, actual access evidence and report paths. Keep credentials in ForgeX model settings. The example deliberately has empty paid credentials/model choices and an unconfirmed NVIDIA access tier.

Run `scripts/qualify-agent-quality.py --help` for the installed application's loopback URL, exact installer, provider/model and execution-profile arguments. Run the `generation`, `maintenance` and `targets` suites separately for **each** model. Preserve failures and complete each report's human review fields; automated passes alone are insufficient.

For each fresh original incident, use an installed, immutable runtime from that exact installer:

```powershell
.forgex/release-tools-python13/python.exe -B scripts/qualify-forgex-runtime.py `
  --fixture tests/fixtures/runtime-incident-smart-monitor.json `
  --packaged-root '<installed resources/forgex>' `
  --installer '<signed installer.exe>' `
  --node-executable '<installed ForgeX.exe>' `
  --output .forgex/incident-final-1
```

Repeat with new output folders for attempts 2 and 3. The runner verifies the installer sidecar, manifest and packaged file hashes before contacting the model, launches bundled Python/PlatformIO with an isolated data profile, and preserves the original project. Omitting packaged arguments runs a source diagnostic, which cannot pass final release qualification. A continuation is useful for diagnosis but cannot count as a fresh incident success.

Assemble existing evidence without declaring a release pass:

```powershell
.forgex/release-tools-python13/python.exe scripts/assemble-reliability-evidence.py `
  --config '<qualification-config.json>' --installer '<signed installer.exe>' `
  --incident-report .forgex/incident-final-1/result.json `
  --incident-report .forgex/incident-final-2/result.json `
  --incident-report .forgex/incident-final-3/result.json `
  --output .forgex/reliability-bundle.json
```

Then supply this bundle as the final argument to `npm run verify:release -- ...`; the verifier's usage output lists the other required evidence files. Assembly does not run models, mark scenarios reviewed, or approve a release.

Published model scopes in `backend/model_router/data/task-qualifications.json` must come from reviewed passing evidence. They bind the backend fingerprint, endpoint, target, framework and profile. The shipped file remains empty until qualification succeeds. After changing packaged metadata, rebuild and qualify the exact final installer/resource manifest again.

## Operational controls and evidence

Set `FORGEX_ENABLE_STRUCTURED_GENERATION=0` to disable the new scheduling controller for investigation. This does not bypass permissions, budgets, build validation or reviews. Preserve data and draft checkpoints before rollback; an older controller must not resume a future checkpoint version.

Task evidence is under `.forgex/production-reliability-20261018/`; source diagnostic projects use `.forgex/qr2/` and `.forgex/qr3/` to avoid adding test-harness path pressure. The first two replays failed and remain retained: the first at Windows dependency-file creation, the second after repeated unmatched edits. They are not successful qualification evidence. Source fingerprints distinguish these diagnostic revisions from the final implementation.

## Validation results

| Check | Result |
| --- | --- |
| Final backend suite, Python 3.13 | 2,710 passed in 622.07 seconds; four Windows symlink skips; two dependency deprecation warnings |
| Controller, evidence, dependency and model-selection checks, Python 3.10 | 51 passed |
| Production renderer browser scenarios | 66 passed together; added connection-versus-qualification scenario passed separately (67 total) |
| Frontend state checks | 104 passed |
| Model router / Electron / harness / local read recovery | 49 / 45 / 12 / 5 passed |
| Release script tests | 39 passed |
| Production frontend build | Passed with one build worker and bounded Node heap |
| Frontend type checking / lint | Passed with zero lint warnings |
| Staged runtime content integrity | Passed; 5,395 manifest files, including embedded startup configuration |
| Bundled backend smoke | Authentication, per-user storage, tool catalog, selected environments and restart persistence passed |
| Bundled durable recovery smoke | Stop/outage/restart/resume/discard passed using explicitly injected provider outcomes |
| Bundled real ESP32 compiler smoke | Build, partition preflight, firmware hashes and sealed upload set passed; no hardware operation |
| Real registry range validation | All four Adafruit constraints accepted with configuration bytes unchanged; no model or hardware qualification |

The candidate is `.forgex/release-staging/92e99759b48c48869e4fd98a2a127a95/forgex`. It is an unpackaged review candidate, **not a signed installer**. Manifest SHA-256 `54695a737196775656ce99c610e33921cbd152be50323cce3e060b3a2e1be4c8` binds packaged backend fingerprint `278fe1fd94ba9a860b495b8f85e93892968f9cf26116c253b1a60c029afb5d25` to development fingerprint `79ff7d5bd8f8b25b58465b243f134f34780b7f4b40c5f5f5a867f1892653f266`. The difference reflects the release input files included by the existing fingerprint contract; compare each report against the runtime it actually used.

The third investigative Smart Monitor diagnostic (`.forgex/qr3/result.json`), before the final evidence-type and review-handoff changes, **failed** with `AGENT_RUNTIME_NO_PROGRESS`: 762.60 seconds, 37 physical model requests and 43 tools, all within the unchanged standard limits. The compiler still reported incompatible module interfaces and missing declarations; repeated unchanged edits did not repair them. No firmware, requirement review or completion review passed. Backend health had zero failures and a 33 ms p95 response time. This is a model task-completion failure, not a successful reliability qualification.

Two smaller live diagnostics isolated additional host defects. The first compiled valid firmware but rejected “run the local build” as a source-code citation obligation. After the evidence-type migration, the second compiled and accepted all four requirement references, but repeated validation never entered independent review and stopped after 185.06 seconds. These failures are retained in `.forgex/qsmall-20261018/` and `.forgex/qsmall2-20261018/`; neither is passing qualification evidence. They led to the evidence-type and automatic handoff fixes above.

The third small diagnostic entered independent review and hit its 60-second response deadline. Normal continuation then completed compilation and independent requirement review, with matching source/task revisions and a verified firmware hash, after 323.88 cumulative seconds, 20 model requests and 17 tools. The original 900-second budget was retained. This continuation is a successful limited workflow check before the final model-inheritance and dependency changes; it is not a fresh Smart Monitor success, a separate completion-review pass, or production model qualification. Evidence: `.forgex/qsmall3-20261018/continuation-341e4cf6d10a4667a3373138af6fc1e8/result.json`.

The fourth Smart Monitor replay stopped after 133.95 seconds during dependency setup. It exposed the exact-version registry lookup and distinct-package progress defects above. Its failed result remains in `.forgex/qr4/result.json`; the owned registry fixture in `.forgex/qdeps1-20261018/result.json` verifies the range fix without changing generated incident firmware.

The fifth, final-source Smart Monitor replay (`.forgex/qr5/result.json`) passed dependency setup and reached the compiler, then **failed** with `provider_requested_forbidden_tool` after 489.53 seconds, 19 model requests and 22 tools. Generated code still disagreed about `ButtonState`, the `Config` namespace/macros and `SystemStatus` members. After an exact edit failed, the model twice requested an edit method that bounded recovery had removed, including after its correction attempt. The runtime retained the allowed tool set and saved the draft. No firmware or independent review passed. All health checks succeeded, with a 23 ms p95. This is not a successful repair of the original generation incident.

A final-source fresh small diagnostic (`.forgex/qsmall4-20261018/result.json`) compiled valid firmware but ended with `TOOL_ARGUMENTS_INVALID` before independent verification, after 120.78 seconds. Its report is also a failure. The retained successful small-task continuation does not erase these fresh-attempt failures or establish model reliability.

## Remaining acceptance blockers

1. The original NVIDIA model has not produced the required three fresh, complete Smart Monitor successes within 900 seconds. Five retained investigative replays all failed across successive source revisions; the fifth uses the final backend fingerprint. Further work on model planning, coherent interface generation and repair-tool compliance is needed before claiming this incident is solved.
2. Separate reviewed free and paid generation, maintenance and target reports are missing. No paid provider/model or usage limit was supplied; access tiers remain unconfirmed. These cannot be inferred from catalog prices or substituted with synthetic tests.
3. `CSC_LINK` and `CSC_KEY_PASSWORD` are not configured in this environment. No signed installer was produced or verified.
4. Clean Windows 10/11 and physical hardware evidence are absent. Existing hardware gates remain 20 builds and 10 nonce-verified cycles. Four local symlink tests also require a Windows environment permitting their execution.

The original production orchestration database and original generated project are retained; no generated firmware was manually repaired to manufacture model success. `task-provenance.json` and `task-only.patch` under the task evidence directory distinguish this implementation from pre-existing user changes.

The final complete suite is recorded in `.forgex/production-reliability-20261018/backend-completion-final.xml`. Earlier interrupted invocations are not passing evidence. A preceding full invocation exposed a migration test hard-coded to evidence version 2; its assertion now uses the authoritative contract version, and the complete final rerun above passes. `execution-summary.json` records the final runtime/candidate hashes, local validation and unmet release acceptance; it explicitly sets `production_qualified` to `false`.
