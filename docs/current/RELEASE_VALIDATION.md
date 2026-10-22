# ForgeX Phase 6 Validation

This source release was rebuilt and validated from a clean working tree.

## Passed gates

- Python repository: 1552 / 1552 tests passed.
- Model Runtime / Agent / API deterministic-routing regressions are included in that suite.
- Harness: 9 / 9 tests passed using the system Node type declarations.
- Electron core manager tests: 29 / 29 passed. The test compilation used a temporary local Electron API shim only to compile manager modules that do not require a live Electron runtime; the shim is not included in this release.
- Dependency-free frontend state tests: 56 / 56 passed.
- TypeScript/TSX syntax parsing: 114 source files passed using TypeScript 5.8.3.
- Release-script tests: 15 / 15 passed.
- Source release content scan: passed.
- Clean source archive startup: passed.
- Clean extracted archive Python suite: required before final artifact delivery.

## Environment-blocked gates

A dependency-installed `npm ci`, Next.js typecheck/lint/production build, React render test, and full Electron package build could not be executed in this environment because `https://registry.npmjs.org` was unreachable (`npm ping` timed out) and the local npm cache did not contain all required packages. These checks are **not reported as passing**. Run them on a machine with npm registry access before publishing signed desktop binaries.

## Release rule

ForgeX never automatically changes provider/model after a selected model fails or is rate-limited. Rate-limit/quota failures pause the Agent run, preserve the session/model binding, and require same-model retry or an explicit user model change.
