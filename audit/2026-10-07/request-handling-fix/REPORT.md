ForgeX request-handling fixes — 7 October 2026

Implemented in ForgeX's application code. Existing generated firmware projects and saved drafts were not manually edited, rebuilt, published, or flashed. API rate-limit policies remain unchanged.

Verification arguments and recovery

- The live failure supplied `verify_requirements.checks` as a JSON string rather than an array. ForgeX now decodes one JSON layer for schema-declared structured fields before validating the tool call.
- Invalid JSON, duplicate keys, nonfinite values, excessive payloads, incorrect types, missing evidence and invalid statuses remain errors. File contents and source evidence strings are preserved.
- Generation uses the remaining job time after reserving host verification time. A primary request leaves time for a corrective request, whose deadline remains bounded by the turn, job and 120-second repair maximum. A useful repair can start with at least 30 seconds left.
- Existing request, token, input and physical-attempt budgets remain enforced. Independent requirement review and firmware verification remain necessary for verified completion.
- Real worker tests reproduce the JSON-string checks and the recorded 470.766 seconds remaining with a 180-second verification reserve. Both normalization and corrective recovery pass.

Provider model catalogs

- Cached catalogs return without waiting for provider discovery. Refreshes share one task per provider, have a 10-second discovery deadline, preserve cached models on failure, and are cancelled during application shutdown.
- SQLite-backed catalog reads reuse a coherent snapshot and run on a worker thread. This also covers the complete `/v2/models/catalog` endpoint, so catalog reads leave the API event loop available for health requests.
- Explicit refresh remains available with `?refresh=true` on the provider model-list endpoint.
- The smoke test uses a read-only copy of the live 466-model catalog, an isolated SQLite database, and an authenticated HTTP backend. It sends no inference requests.

| Request | Final measured time |
| --- | --- |
| Cached OpenRouter model list, three concurrent reads | 1.1083–1.5025 seconds |
| Median cached model-list response | 1.4184 seconds |
| Concurrent health requests | 0.3503–0.4193 seconds |
| Complete provider catalog | 2.2621 seconds |

The original provider model-list requests took 33.328 and 33.938 seconds. These measurements concern catalog loading; they do not measure model inference latency.

Long instructions

- Agent session messages, direct run instructions, Chat messages and saved-message storage share a 65,536-character limit.
- The composer counts Unicode characters consistently with the backend, displays the limit, blocks oversized submissions and retains their drafts. Agent and Chat hooks also validate before sending.
- API validation errors identify the rejected field. Failed submissions preserve the draft.
- The direct-run body cap was 32 KB. Message endpoints now have a bounded byte cap that accommodates the full character limit even when astral Unicode characters use JSON escape sequences. Chunked bodies are bounded too.

Validation

- 312 distinct backend tests passed across the broad regression suite and the final catalog/API follow-up. One Windows symlink test was skipped because symlink creation was unavailable.
- 86 UI unit tests and all 38 conversation browser tests passed.
- Frontend type checking, frontend linting and `git diff --check` passed.
- Browser regressions cover oversized drafts surviving reload, longer valid instructions being sent, specific server errors preserving drafts, and session recovery following its replacement run.
- The final desktop restart was permitted only after confirming there were no active agent runs.
- The running backend reports `healthy`, `engine_ready: true`, and the same source fingerprint as the isolated smoke test:
  `38307256e22e865598ad26083a1c353340b78de8bb29cb8fcf2588f294b8c4e2`.
- Post-restart logs contain no backend errors and no renderer request deadlines. One readiness request was cancelled with `AbortError` after 74 ms; it was not a timeout.

Evidence files: `python-tests.xml`, `catalog-api-tests.xml`, `runtime-validation.json`, `live-health.json`, `live-log-validation.json`, and `launch-receipt.json` in this folder. The historical failed run was left intact; these changes apply to future ForgeX requests.
