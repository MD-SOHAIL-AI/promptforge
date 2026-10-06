# ForgeX agent quality and durable jobs

The implementation keeps one saved job above its linked run attempts. It stores
the job, recovery policy, task milestones, cumulative tool/model/time budgets,
retry deadline and project trust in the existing SQLite journal. Version 2
databases receive a backup before the additive version 3 migration. Newer
databases and unknown checkpoint formats cannot be silently downgraded.

## Implemented behavior

| Area | Behavior |
| --- | --- |
| Task interpretation | Deterministic routing handles known actions and prohibitions. Unknown requests may use a bounded, tool-free interpretation with the selected model. Requirements and choice options must occur in the actual request. Interpretation cannot authorize flash or provider changes. |
| Follow-ups | Requirements retain their IDs and content hashes; new instructions create a task revision and retain earlier exclusions and accepted choices. The latest request takes precedence over conflicting earlier requirements. Up to 64 requirement groups are supported. |
| Recovery | Safe automatic recovery is enabled for new jobs, after the deterministic restart matrix passed. Migrated jobs remain manual. A sealed checkpoint, unchanged workspace/stage, project trust, remaining budget and known operation outcomes are required. User pauses persist. Flash workflows require manual recovery. |
| Provider interruption | Eligible transient failures retain a retry deadline, respect Retry-After, use increasing delay and allow at most three automatic continuations for the job. Missing credentials, quota exhaustion and invalid model output do not receive this automatic retry. Provider and model selection remain explicit. |
| Budgets | Tool calls are reserved before execution. Model requests reserve their maximum output before outbound work, including retries and child planners. Counters survive reopening and explicit retry. Extensions require an idempotency key and are capped. Interpretation has a separate maximum of three physical requests. |
| Shutdown | ForgeX drains/cancels active work, retains the last settled checkpoint, and releases its lease. Completed tasks retain their completion status. No tray service continues work after quit. |
| Review | Complex firmware specifications with at least eight requirement groups receive bounded read-only review before completion. Reviewers cannot write or create children. Parent and children share budgets; the parent allows at most two child slots. Review findings can trigger another bounded repair/review attempt. |
| Context and memory | Context compaction keeps recent detail, discoveries, failed approaches, task revision and accepted choices. Omitted observations are retrievable from journal artifacts. Oversized observations are bounded. Named user choices retain user provenance and revised choices invalidate older entries. |
| Evidence | Source quotes, current firmware builds, native tests and simulation have distinct evidence. Writes invalidate earlier execution evidence. A compile alone cannot satisfy a requested unit-test check. The UI displays acceptance results separately. |
| Retention | After 30 days, settled unpinned jobs may retire checkpoints when no recovery draft, ChangeSet or uncertain operation references them. Retired private checkpoint references are removed so orphan stage cleanup can operate. Audit events and context artifacts are retained. |

## Added tools

| Tool | Scope and limitations |
| --- | --- |
| `project_doctor` | Bounded inspection of configuration, environments, source and embedded warnings; host tool readiness remains separately reported. |
| `inspect_symbols` | Lexical source references and line hints; not a compiler or language-server index. |
| `resolve_dependency` | PlatformIO registry search. It never installs a package by itself. |
| `search_docs` / `fetch_docs` | Curated official documentation catalog and bounded HTTPS fetches. DNS addresses and redirect targets are checked; private addresses are blocked. Documentation remains untrusted data. |
| `validate_hardware_config` | Source heuristics for classic ESP32 pins and peripherals. It does not certify physical wiring. |
| `run_unit_tests` | An existing native PlatformIO environment in a trusted project; no device upload. Requires a native compiler and an executed passing runner summary. No tests, skipped tests and missing tools cannot count as success. |
| `analyze_firmware` | Current build diagnostics and source-bound artifact/test evidence. |
| `observe_serial` | Non-destructive bounded snapshot of an already connected monitor's buffer; never opens or resets a port. |
| `decode_crash` | Typed addr2line invocation using the exact ELF hash from the current build. |
| `verify_acceptance` | Host-collected evidence for the current task/source revision. The model cannot submit an invented passing test result. |

The shared catalog advertises host tools only when their handlers and run
prerequisites are available. Arbitrary shell tools, unrestricted MCP connections,
browser write tools, downloaded-code sandboxing and file-writing child agents
remain outside this ESP32 beta scope. Trusted local execution is not an OS
sandbox; trust projects only when you intend their code to execute locally.

## Qualification and release gates

`tests/fixtures/agent-quality-suite.json` contains 30 versioned scenarios. The
first five preserve the historical incident prompts exactly. Each scenario
specifies its expected behavior, forbidden changes and evidence. The runner
creates isolated projects, preserves a sentinel user file, captures diagnostics
and executes each scenario three times with the explicitly selected real model.

```powershell
$env:FORGEX_LOCAL_TOKEN = '<local desktop token>'
.forgex/release-tools-python13/python.exe scripts/qualify-agent-quality.py --installer <installer.exe> --workspace <qualification-directory> --provider <provider-id> --model <model-id> --execution-profile advanced --output <agent-quality.json>
node scripts/verify-agent-quality.mjs <installer.exe> <agent-quality.json>
python scripts/qualify-agent-quality.py --suite maintenance --installer <installer.exe> --workspace <maintenance-directory> --provider <provider-id> --model <model-id> --output <agent-maintenance.json>
node scripts/verify-agent-quality.mjs <installer.exe> <agent-maintenance.json> maintenance
npm.cmd run verify:release -- <installer.exe> <hardware.json> <clean-machines.json> <agent-prompts.json> <agent-quality.json> <agent-maintenance.json>
```

The local token comes from the environment and is excluded from reports. Model
usage charges apply. A report must match the fixture hash, installer hash,
packaged resource manifest and backend source fingerprint. Qualification
requires at least 86 of 90 passing attempts, at least two passes for each scenario, every historical incident repetition
passing, and recorded human review of requirement fidelity. Mock-based unit and
browser tests do not substitute for this real-provider qualification.

The separate maintenance fixture has 20 existing-project scenarios, three
repetitions each, and requires 57 of 60 passing attempts. Every scenario needs
two passes, and every safety-critical repetition must pass. The release verifier
requires both generation and maintenance reports for the same installer and
runtime. A matching source quote is only a reference; fresh `model_review`
evidence is required wherever the suite demands requirement fidelity.

Release still requires the same signed installer to pass physical ESP32 build,
flash/serial cycles and clean Windows 10/11 installation, upgrade, offline and
recovery checks. Those external sign-offs are not established by implementation
or by an unsigned development installer.

## Rollback switches

Set `FORGEX_ENABLE_SAFE_AUTO_RECOVERY=0` to keep recovery manual. Set
`FORGEX_ENABLE_COMPLEX_REVIEW=0` to disable automatic complex-task review during
diagnosis. Project trust is required by the desktop executor; the compatibility
switch `FORGEX_REQUIRE_PROJECT_TRUST=0` is for controlled development only.
Preserve the database backups and user workspace when rolling back. A version 2
backend cannot read the version 3 job database; restore a tested backup rather
than deleting user state.

The implementation cannot guarantee perfect output for every prompt. The beta
qualification measures the supported ESP32 scope, while blocked prerequisites
and unverified behavior must remain visible to the user.
