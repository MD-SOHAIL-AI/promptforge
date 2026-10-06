from backend.agent_runtime.workspace_index import WorkspaceIndex


def test_workspace_index_reuses_only_matching_snapshot_revision(tmp_path, monkeypatch):
    source = tmp_path / "main.cpp"
    source.write_text("int alpha;", encoding="utf-8")
    first = WorkspaceIndex(tmp_path, revision_hint="source-a")
    assert first.query("alpha")[0].path == "main.cpp"

    same = WorkspaceIndex(tmp_path, revision_hint="source-a")
    monkeypatch.setattr(same, "rebuild", lambda: (_ for _ in ()).throw(AssertionError("rebuilt")))
    assert same.query("alpha")[0].path == "main.cpp"

    source.write_text("int beta;", encoding="utf-8")
    changed = WorkspaceIndex(tmp_path, revision_hint="source-b")
    assert changed.query("beta")[0].path == "main.cpp"
    assert changed.query("alpha") == ()
