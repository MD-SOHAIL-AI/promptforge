# ForgeX Runtime Threat Model

Status: implemented baseline, 2026-07-12

## Assets and trust boundaries

ForgeX protects OS credentials, CLI-owned sessions, source workspaces, firmware artifacts, connected hardware, durable workflow state, approvals, and routing/disclosure decisions. Model providers and Agent Adapters are untrusted proposal producers. Apply, build, flash, monitor, approvals, active-workspace mutation, and credential access remain ForgeX-owned authorities.

Trust boundaries exist at HTTP/WebSocket clients, remote model APIs, local CLI processes, disposable sandboxes, the OS credential store, SQLite persistence, serial devices, and diagnostic export.

## Threats and controls

| Threat | Control |
|---|---|
| Credential or auth-output disclosure | Diagnostics export only safe connection summaries and aggregate log counts. Secret-named keys, known secret patterns, environment values, prompts, responses, raw source, and raw logs are excluded or redacted. CLI auth files are never read. |
| Prompt/source leakage through telemetry | Runtime diagnostic export contains no log bodies, prompts, responses, source text, diffs, or unrestricted paths. Metrics use a fixed low-cardinality name set with integer counters only. |
| Provider hangs or cascading failures | Every provider call has a wall-clock timeout, bounded sliding-window rate limit, budget, and per-provider circuit breaker with half-open recovery. Authentication and policy failures remain non-fallback conditions. |
| Resource exhaustion | Profile/model budgets constrain wall time, steps, tokens, and cost. Logs, WebSocket queues, diagnostics, provider scopes, backups, outputs, and retained records are bounded. |
| Duplicate or raced commands | Workflow transitions require expected state/version and idempotency keys. SQLite transactions, WAL, foreign keys, operation leases, and monotonic events fail closed under contention. |
| Lost WebSocket delivery | State and events commit before outbox fan-out. Queue overflow disconnects the slow subscriber with a replay-required signal; clients replay by sequence. |
| Process escape or orphaning | Managed subprocesses use scrubbed environments, process groups/tree termination, cancellation cleanup, time/output limits, and disposable sandboxes. |
| Database corruption or loss | Online SQLite backups are integrity checked, atomically published, rotated, and used only after primary corruption is detected and a backup passes read-only integrity validation. Corrupt primary and WAL/SHM sidecars are quarantined. |
| Stale operations after restart | Startup reconciliation expires approvals and leases, fails stale runs without a live lease, applies safe retention, creates a verified backup, and resumes outbox publication. |
| Unsafe hardware continuation | Flash approvals bind artifact/device/port/board/command hashes. Device disappearance fails the executor; it never silently chooses another port or board. |
| Telemetry changes outcomes | Metric and structured-log emission are best-effort. Telemetry exceptions are swallowed at observation boundaries and never replace provider or workflow results. Database safety failures remain explicit operational failures. |

## Retention and recovery

Published workflow events remain append-only and are not deleted by routine retention. Retention removes only expired command deduplication records, already-published outbox rows, and aged usage summaries. Backup rotation keeps a bounded number of verified snapshots. Recovery never overwrites a healthy database and never restores an unverified backup.

## Diagnostic export contract

GET /diagnostics/export returns a bounded JSON attachment containing schema/runtime versions, SQLite mode and aggregate counts, fixed-name metrics, circuit states, safe Connection Registry diagnostics, aggregate log counts, and the last reconciliation report. It explicitly excludes credentials, secret environment values, raw authentication output, prompts, responses, source, diffs, and raw logs.

## Residual risks

Local administrators can inspect process memory and files outside ForgeX controls. Third-party provider behavior and upstream CLI implementation remain external risks. WAL backups are point-in-time logical copies, not remote disaster recovery. Production eligibility for local CLI adapters remains disabled until containment and parity evidence passes.
