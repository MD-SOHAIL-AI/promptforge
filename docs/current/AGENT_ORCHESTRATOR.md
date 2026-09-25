# ForgeX Agent Orchestrator

`AgentOrchestrator` is the sole owner of action-run lifecycle state. `ProductAgentService` is a compatibility facade. Persisted run changes are projected into `AgentSessionStore` through `AgentSessionRunProjector`; no separate 100 ms run-to-session synchronization worker owns lifecycle state. Agent sessions preserve their exact provider/model binding. Flash approval is bound to the durable workspace revision and verified firmware digest.
