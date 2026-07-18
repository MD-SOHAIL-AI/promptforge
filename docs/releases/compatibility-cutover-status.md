# Compatibility cutover status

Validated on 2026-07-12.

## Regression evidence

- Backend: `2278 passed, 13 skipped` (`python -m pytest -q`).
- Frontend typecheck and lint: passed.
- Authoritative replay/control-plane state tests: 8 passed.
- Targeted registry, routing, AGY, and migration tests: 50 passed.

## Cutover decision

The compatibility cutover is **blocked**. The executable report in `compatibility-cutover-audit.json` is authoritative for the current checkout. Codex model routing and browser-local conversation authority are absent, but duplicate registries, JSONL workflow authority, legacy panels/flags, and the fail-closed unified executor remain until runtime parity is proven.

Do not delete retained paths merely because regression tests pass. Complete executor parity, migrate and verify every durable run, rerun the caller inventory and full suite, take a database backup, and follow the rollback procedure in `../operations/compatibility-cutover.md`.