# ForgeX Phase 6 Release Contract

A ForgeX source release must omit dependency trees, build output, caches, local environment files, runtime workspaces, generated firmware, and nested review archives. The source manifest enumerates architecture-critical files. Release validation includes Python tests, dependency-free frontend tests, release-script tests, source scan, clean extraction, backend startup, and clean-extraction Python tests. Dependency-installed Next/Electron builds must be reported separately if the npm registry is unavailable.
