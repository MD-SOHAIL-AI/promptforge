# ForgeX project snapshot for ChatGPT review

This ZIP contains the current working files, including uncommitted changes and new source files, as of 2026-10-20.

Start with README.md, package.json, frontend/package.json, pyproject.toml, requirements*.txt / *.lock, and docs/current/.
Review backend/, frontend/, electron/, harness/, model-router/, scripts/, tests/, examples/, and third_party/ together.
The archive includes documentation, audit reports, source assets, dependency manifests and lockfiles, and .env.example.

Excluded: node_modules and Python virtual environments; real .env files and private key files; Git history; caches;
Next.js and other build output; generated Monaco assets; local application state and generated workspace projects;
logs and runtime event streams; local databases and audit runtime settings; downloaded audit tooling; old ZIPs;
compiled binaries, firmware output, and temporary files. No dependencies are installed in this snapshot.
Workspace .gitkeep placeholders are retained where present. Monaco assets are recreated by scripts/prepare-frontend-assets.mjs.

REVIEW_FILE_MANIFEST.txt lists every included project file with its size and SHA-256 hash.

Suggested review request:
Review this complete project for architecture, correctness, security, reliability, frontend/backend integration,
test coverage, and release readiness. Trace key user flows across components. Prioritize findings by severity,
reference exact file paths and lines, and distinguish confirmed bugs from assumptions. Use source code as the
primary evidence; prior reports may describe an older version. Do not assume that tests pass without running them.
