# ForgeX Backend API

The FastAPI application exposes one typed composition object, `ForgeXServices`, at `app.state.services`. HTTP routes resolve dependencies from this container. `/agent-runtime` is the canonical Agent API; the duplicate `/api/v2/agents` surface is retired. `backend.api` uses lazy package exports so importing helpers does not eagerly construct the complete application graph.
