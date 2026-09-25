import pytest

from backend.model_router.selection import ModelRef, ModelSelectionStore


@pytest.mark.parametrize("operation", ["get", "set", "clear", "resolve"])
@pytest.mark.parametrize("session_id", ["", " ", " padded", "x" * 257, "bad\x00id"])
def test_invalid_scope_never_aliases_application(tmp_path, operation, session_id):
    store = ModelSelectionStore(tmp_path / "selection.sqlite3")
    model = ModelRef("provider", "preserved")
    store.set(model)
    args = (model,) if operation == "set" else ()
    with pytest.raises(ValueError, match="model_session_invalid"):
        getattr(store, operation)(*args, session_id=session_id)
    assert store.get() == model


@pytest.mark.parametrize("operation", ["get", "set", "clear", "resolve"])
def test_invalid_role_rejected_consistently(tmp_path, operation):
    store = ModelSelectionStore(tmp_path / "selection.sqlite3")
    args = (ModelRef("provider", "model"),) if operation == "set" else ()
    with pytest.raises(ValueError, match="model_role_invalid"):
        getattr(store, operation)(*args, role="invalid")


def test_empty_store_reports_task_route_inheritance(tmp_path):
    store = ModelSelectionStore(tmp_path / "selection.sqlite3")
    assert store.resolve() == (None, "task_route")
    assert store.resolve(session_id="session", role="review") == (None, "task_route")
