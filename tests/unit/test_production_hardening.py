"""Cross-component regressions for production review findings."""
from pathlib import Path

import pytest

from backend.agent_runtime.embedded_context import inspect_embedded_project
from backend.agent_runtime.risk import RiskAssessmentService
from backend.changes import ChangeSetError
from tests.unit.test_changeset_service import prepare_change
from tests.unit.test_build_firmware import FakeSubprocessManager, make_project, run_build
from backend.tools.build_firmware import BuildConfig, FirmwareBuilder


@pytest.mark.parametrize("before,after", [
    ("x" * 512_001, "secret = 1;\n"),
    ("old\n" * 20_000, "new\n" * 20_000),
], ids=["unavailable", "truncated"])
def test_incomplete_diff_never_auto_applies(tmp_path: Path, before: str, after: str):
    _, _, _, _, change = prepare_change(tmp_path, before=before, after=after)
    risk = RiskAssessmentService().assess(change, explicit_edit_authorized=True)
    assert "incomplete_change_analysis" in risk.reasons
    assert risk.user_approval_required and risk.reviewer_required
    assert not risk.scoped_auto_apply_allowed


@pytest.mark.parametrize("missing", [False, True])
def test_invalid_undo_backup_preserves_applied_files(tmp_path: Path, missing: bool):
    service, _, target, stage, change = prepare_change(tmp_path)
    service.apply(change.change_set_id)
    backup = stage / "__undo__" / "src" / "main.cpp"
    if missing:
        backup.unlink()
    else:
        backup.write_text("corrupted", encoding="utf-8")
    with pytest.raises(ChangeSetError, match="backup"):
        service.undo(change.change_set_id)
    assert target.read_text(encoding="utf-8") == "new\n"
    assert service.get(change.change_set_id).status == "applied"
    assert stage.exists()


def test_dependencies_cannot_displace_application_hardware_context(tmp_path: Path):
    dependencies = tmp_path / ".pio" / "libdeps"
    dependencies.mkdir(parents=True)
    for index in range(90):
        (dependencies / f"{index}.cpp").write_text("// dependency", encoding="utf-8")
    source = tmp_path / "src"
    source.mkdir()
    (source / "main.cpp").write_text("#define LED_PIN 23\n", encoding="utf-8")
    graph = inspect_embedded_project(tmp_path)
    assert any(pin.symbol == "LED_PIN" and pin.pin == 23 for pin in graph.pins)


@pytest.mark.parametrize("option", ["targets=upload", "extra_scripts=pre:hook.py", "platform_packages=custom", "extra_configs=other.ini"])
def test_build_rejects_executable_configuration_before_process(tmp_path: Path, option: str):
    project = make_project(tmp_path / "firmware")
    ini = project / "platformio.ini"
    ini.write_text(ini.read_text(encoding="utf-8") + "\n" + option + "\n", encoding="utf-8")
    manager = FakeSubprocessManager()
    result = run_build(manager, BuildConfig(project_dir=project, environment="esp32dev"))
    assert not result.success
    assert not manager.calls


def test_build_uses_platform_default_build_graph(tmp_path: Path):
    args = FirmwareBuilder._build_args(BuildConfig(project_dir=tmp_path), tmp_path)
    assert args == ["pio", "run", "--project-dir", str(tmp_path)]
    assert "--target" not in args


@pytest.mark.parametrize("reload_before_save", [False, True])
def test_changeset_addition_survives_managed_save(tmp_path: Path, reload_before_save: bool):
    from backend.changes import ChangeSetService
    from backend.services.project_service import ProjectService
    from tests.unit.test_project_service import project, run
    projects = ProjectService(tmp_path / "projects")
    original = project()
    metadata = run(projects.create_project(original))
    root = Path(metadata.project_path)
    changes = ChangeSetService(state_root=tmp_path / "state", staging_root=tmp_path / "stages")
    baseline = changes.inspect_workspace(root)
    stage = changes.create_stage("addition")
    (stage / "helper.cpp").write_text("// keep me\n", encoding="utf-8")
    change = changes.create_from_stage(provider_id="test", workspace_root=root, stage_root=stage, baseline=baseline, authorized_paths={"helper.cpp"})
    changes.apply(change.change_set_id)
    loaded = run(projects.load_project(original.project_name))
    assert any(file.path == "helper.cpp" for file in loaded.files)
    run(projects.save_project(loaded if reload_before_save else original))
    assert (root / "helper.cpp").read_text(encoding="utf-8") == "// keep me\n"


def test_autonomous_loop_loads_skills_from_original_workspace(tmp_path: Path):
    from backend.agent_runtime.autonomous_loop import AutonomousForgeAgentLoop
    root = tmp_path / "project"
    skill = root / ".forgex" / "skills" / "custom"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# Custom review\nRead the driver.", encoding="utf-8")
    stage = tmp_path / "stage"
    stage.mkdir()
    loop = AutonomousForgeAgentLoop(workspace_root=stage, skill_root=root, planner=object(), platformio=object(), progress=None)
    assert "custom" in {item.name for item in loop.skills.discover()}
    assert "Read the driver." in loop.skills.load("custom")
    assert not (stage / ".forgex").exists()
