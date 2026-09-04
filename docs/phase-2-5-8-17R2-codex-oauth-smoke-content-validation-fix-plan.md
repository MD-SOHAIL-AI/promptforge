# Phase 2.5.8.17R2 - Codex OAuth Smoke Content Validation Fix Plan

## Objective

Diagnose the prior `CODEX_OAUTH_SMOKE_CONTENT_INVALID` result without exposing file content, accept only harmless BOM/newline encoding differences, tighten the runtime-only smoke instruction, and permit one guarded retry after all non-real gates pass.

## Controls

- Inspect only the exact run identifier in ForgeX's sanitized last-smoke metadata under the fixed managed smoke root.
- Report hashes, byte/line counts, newline/BOM/whitespace flags, and a mismatch category only.
- Remove only a UTF-8 BOM, normalize CRLF to LF, and remove terminal LF characters before exact comparison with the expected logical line.
- Reject extra text, extra text lines, markdown, quotes, leading whitespace, trailing spaces, capitalization changes, and punctuation changes.
- Use prompt variant `strict_single_line_v2` without persisting the prompt.
- Create a Bridge Review only after an exact one-file normalized pass with unchanged marker and active workspace.
- Keep production routing, apply, build, and flash disabled.
