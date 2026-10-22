# DeepSeek Harness provenance

ForgeX's TypeScript harness is an internal, reviewed architectural fork of
DeepSeek Harness `0.1.5-rc.2`. The reference archive is identified by the
hashes in `PIN.json`; ForgeX does not read or execute code from the user's
Downloads directory.

The first imported design surface is intentionally small and auditable:

- lifecycle-owned runtime capabilities;
- append-only session events and replayed projections;
- guarded tool execution with immutable final results;
- provider-neutral model routing;
- isolated subagent and workflow seams;
- fail-closed process and hardware boundaries.

Upstream source remains MIT licensed. Forge-specific implementations and
patches live under `harness/` and retain this notice in distributions.
