import asyncio

from backend.model_router.models import ModelRequest
from backend.model_router.selection import ModelRef, ModelSelectionStore
from tests.unit.test_model_runtime_v2 import Provider, build


def test_selection_precedence_and_restart(tmp_path):
    path = tmp_path / "selection.sqlite3"
    selections = ModelSelectionStore(path)
    selections.set(ModelRef("openai", "default"))
    selections.set(ModelRef("openai", "session"), session_id="s")
    selections.set(ModelRef("openai", "review"), role="review")
    restored = ModelSelectionStore(path)
    assert restored.resolve(session_id="s")[0].model_id == "session"
    assert restored.resolve(session_id="s", role="review")[0].model_id == "review"
    assert restored.resolve()[0].model_id == "default"
    restored.set(ModelRef("openai", "session-review"), session_id="s", role="review")
    assert restored.resolve(session_id="s", role="review")[0].model_id == "session-review"


def test_gateway_uses_role_but_honors_explicit_choice(tmp_path):
    adapter = Provider("openrouter")
    router, _ = build(tmp_path, {"openrouter": adapter})
    router.selections.set(ModelRef("openrouter", "role-model"), role="review")
    result = asyncio.run(router.generate_model(ModelRequest(prompt="review", metadata={"agent_role": "review"})))
    assert result.model_id == "role-model"
    explicit = asyncio.run(router.generate_model(ModelRequest(
        prompt="review", provider_id="openrouter", model_id="explicit-model", metadata={"agent_role": "review"},
    )))
    assert explicit.model_id == "explicit-model"


def test_clear_binding_restores_inheritance_without_deleting_other_roles(tmp_path):
    store = ModelSelectionStore(tmp_path / "selection.sqlite3")
    store.set(ModelRef("openai", "global"), role="review")
    store.set(ModelRef("openrouter", "local"), session_id="s", role="review")
    assert store.clear(session_id="s", role="review") is True
    assert store.clear(session_id="s", role="review") is False
    assert store.resolve(session_id="s", role="review")[0].model_id == "global"
    assert not store.references("openrouter")


def test_subagent_role_prefers_session_binding_and_keeps_parent_pinned(tmp_path):
    from backend.agent_runtime.router_planner import RouterProductPlanner
    router, _ = build(tmp_path, {"openrouter": Provider("openrouter")})
    router.selections.set(ModelRef("openrouter", "global-review"), role="review")
    router.selections.set(ModelRef("openrouter", "session-review"), role="review", session_id="s")
    parent = RouterProductPlanner(router, provider_id="openrouter", model_id="parent", session_id="s")
    reviewer = parent.for_role("review")
    explorer = parent.for_role("explore")
    assert reviewer.model_id == "session-review"
    assert reviewer.to_safe_metadata()["selection_source"] == "session_role"
    assert reviewer.to_safe_metadata()["selection_inherited"] is False
    assert explorer.model_id == "parent"
    assert explorer.to_safe_metadata()["selection_source"] == "task_route"
    assert explorer.to_safe_metadata()["selection_inherited"] is True
    assert parent.model_id == "parent"
