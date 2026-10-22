# Model Router Subscription Bridge Notes

Phase 2 does not implement subscription bridges. It only reserves provider auth types for future local integrations:

- `cli_bridge`
- `oauth`

Future bridge candidates:

- OpenAI Codex Bridge
- Claude Code Bridge
- Antigravity / AGY CLI Bridge
- GitHub Copilot Bridge

Subscription bridges must be local-only and must use the user's own login or local session. ForgeX must never route multiple users through the developer's subscription, shared account, API key, or cloud-hosted bridge.

Any bridge implementation must keep credentials out of renderer storage, avoid logging secrets, and expose only local capability/status metadata through the API.
