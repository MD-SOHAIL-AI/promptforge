# Phase 2.5.8.16B — Codex Login Launcher UX Results

ForgeX may display/copy `codex login` and launch the official CLI only after confirmation, using direct argv, `shell:false`, discarded output, and a neutral temporary cwd. ForgeX does not own OAuth and does not inspect auth storage, browser callback URLs, tokens, cookies, codes, or account identity.

Phase 2.5.8.17 adds a separate post-login status and sandbox-smoke action. A browser success page alone does not unlock it; official `codex login status` must report signed in.

## Phase 2.5.8.17A follow-up

Post-login checks now use the shared aligned status service and a safe Windows user environment. The UI exposes Check Status Again and a development-only sanitized diagnostics action. A signed-out result recommends the official login flow or `codex login --device-auth`; it never exposes raw output or authentication details.

Phase 2.5.8.17B adds a sanitized launcher/session parity comparison. The final status runner is resolved separately from prompt execution; Windows command-shim compatibility for status must never be reused for prompt-bearing execution.
