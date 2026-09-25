# ForgeX Frontend

Nexus is the only production shell. Legacy pre-Nexus shells and floating-window layout state are removed. Model state is separated into `useModelRuntimeState`; provider health and catalog freshness are explicit. Agent UI prioritizes the active session's provider/model, supports `paused_model_unavailable`, retries the same model, and changes model only on explicit user action. ChangeSet loading is centralized in `useAgentChangeSet` and changed-file counts use real backend counts.
