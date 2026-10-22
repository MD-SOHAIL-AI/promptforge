# ForgeX redesign regression ledger

Existing deterministic suites are retained. These entries identify the new boundary regressions and the existing suites that protect adjacent behavior.

| Fault | Acceptance | Evidence |
| --- | --- | --- |
| HTTP 200, complete first write, then partial next response followed by silence | Finite idle error, both streams closed, saved isolated draft retained, attempt ID/timings retained and response content excluded | `test_complete_redesign.py::test_actual_http_stream_survives_planner_and_workspace_adapter` |
| Diagnostics cross execution adapter and restart | Valid physical fields survive run serialization and SQLite reopening; malformed/private fields are excluded | `test_real_tool_adapter_keeps_incident_diagnostics_across_storage`, `test_diagnostics_discard_invalid_fields_without_dropping_valid_evidence` |
| Productive request hits a local turn/job boundary | Correct host code/origin; no provider-outage retry | `test_provider_transport_policy.py`, `test_router_product_planner.py`, failure-contract suites |
| Generation consumes verification allowance | Host blocks dispatch before the reserve; review can use it; exhausted global allowance takes precedence | `test_generation_leaves_verification_budget_but_review_can_use_it`, physical-provider boundary matrix |
| Duplicate physical reservation/settlement or stale checkpoint | One request charge; one confirmed usage settlement; uncertainty stays reserved after reopen | `test_attempt_settlement_is_idempotent_durable_and_never_refunds_unknown_output` |
| Ollama completion or truncation | Stream and nonstream known completed usage settle; truncation stays charged | `test_ollama_known_completed_usage_settles_durable_reservation` |
| Review interrupted after one unit | Resume reuses that sealed unit; source changes or bad checksums require fresh review | `test_review_resumes_sealed_units_and_source_change_invalidates_them` |
| Large source/context | Exact contiguous coverage, bounded units, aggregate bound to current revision, no invented execution claim | `test_large_review_covers_exact_spans_and_aggregates_same_revision` |
| Local include missing | A reviewer verdict cannot turn incomplete dependency coverage into valid certification | `test_missing_local_dependency_cannot_be_certified_by_small_review` |
| Unrelated Markdown already exists | Requested wiring/calibration documentation remains missing | `test_unrelated_markdown_never_satisfies_requested_documentation` |
| Projection reopened repeatedly | Checkpoint reused; only later events reduced; empty-WAL recreation does not trigger full observation validation | `test_incremental_projection_reuses_checkpoint_and_mutations_check_integrity`, isolated benchmark |
| Journal bytes changed after observation | Lease/mutation authorization performs a fresh check and quarantines corruption | Same incremental projection regression and durability suites |
| Build accepted twice, then backend restart | At most one build per ID; completed outcome survives reopen; unfinished operation is interrupted rather than replayed | `test_build_operation_double_accept_and_restart_do_not_replay` |
| Build result refreshed after source edit | GET changes it to stale without another build | `test_addressable_build_is_idempotent_and_invalidates_after_source_edit` |
| Agent build viewed for another environment or changed source | Current-source check returns stale | `test_agent_build_evidence_checks_actual_source_and_environment` |
| Task readiness requested | Local/cached prerequisites reported without model generation | `test_readiness_does_not_generate_model_output` |
| Backend repeatedly exits | Three bounded owned restarts; no fourth automatic dispatch; Stop cancels scheduled restart | `electron/backend-manager.test.mjs`, `backend-recovery.test.mjs` |
| Renderer JSON body never completes | Request deadline ends the caller; late completion cannot become current; mutation outcome is unknown rather than silently replayed | `request-deadline.test.mts`, browser held-response regression |
| Quiet/stalled SSE but no error callback | Reconciliation read becomes stale on its deadline; a later read recovers; Stop stays available | `build-evidence.spec.ts`: held response body |
| Compiler failure or stale success | Correct visible state, no Flash action, no fabricated RAM/flash percentage | `build-evidence.spec.ts`: failed compiler / stale compiler output |
| Refresh a saved manual build | Addressable GET retrieves outcome; POST count stays one; later stale state hides Flash | `build-evidence.spec.ts`: saved manual build refresh |
| Session/project changed during restore | Old observation aborted and fenced; selected chat remains authoritative | Conversation browser restoration and late-response cases |
| Trust changes or uncertain apply/flash | Permission refresh does not execute work; uncertain side effects require inspection | Existing outage/recovery/cancellation, release and browser recovery-control suites |
| Runtime policy/type documents drift | Regeneration matches schemas, frontend types, deadline constants and runtime documents | `test_engine_redesign.py::test_generated_engine_contracts_are_current` |

Tests use isolated workspaces and controlled transports. They do not qualify external provider uptime, an installed release, physical devices or all accessibility behavior. Qualification status is recorded separately in [the release matrix](redesign-qualification-2026-10-04.md).
