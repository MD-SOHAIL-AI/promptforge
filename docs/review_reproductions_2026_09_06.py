"""Temporary-fixture reproductions for REPOSITORY_REVIEW_2026-09-06.md.

These assertions demonstrate current bugs, not desired behavior.
No builds, project hooks, network requests or hardware actions are executed.
"""
from __future__ import annotations

import asyncio
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.agent_runtime.capability_profiles import BUILD_REPAIR_PROFILE
from backend.agent_runtime.embedded_context import inspect_embedded_project
from backend.agent_runtime.risk import RiskAssessmentService
from backend.agent_runtime.skills import SkillRegistry
from backend.changes.service import ChangeSetService
from backend.contracts.generated_project import GeneratedFile, GeneratedProject
from backend.runtime.subprocess_mgr import SubprocessManager
from backend.services.platformio_service import PlatformIOService
from backend.services.project_service import ProjectService
from backend.tools.build_firmware import BuildConfig, FirmwareBuilder


def record(label: str) -> None:
    print(f"REPRODUCED: {label}")


async def reproduce(root: Path) -> None:
    projects = ProjectService(root / "projects")
    metadata = await projects.create_project(GeneratedProject(
        project_id="review-project",
        project_name="review-project",
        target_board="ESP32",
        framework="PlatformIO",
        created_at=datetime.now(timezone.utc),
        files=(
            GeneratedFile("platformio.ini", "[env:review]\nplatform=espressif32\nboard=esp32dev\nframework=arduino\n", "ini"),
            GeneratedFile("src/main.cpp", "void setup() {}\nvoid loop() {}\n", "cpp"),
        ),
    ))
    workspace = Path(metadata.project_path)
    changes = ChangeSetService(state_root=root / "state", staging_root=root / "stages")

    baseline = changes.inspect_workspace(workspace)
    stage = changes.create_stage()
    (stage / "helper.cpp").write_text("int helper() { return 1; }\n", encoding="utf-8")
    change = changes.create_from_stage(
        provider_id="review", workspace_root=workspace, stage_root=stage,
        baseline=baseline, authorized_paths=["helper.cpp"],
    )
    changes.apply(change.change_set_id)
    loaded = await projects.load_project(metadata.project_name)
    assert (workspace / "helper.cpp").is_file()
    assert "helper.cpp" not in loaded.list_files()
    await projects.save_project(loaded)
    assert not (workspace / "helper.cpp").exists()
    record("managed save deletes a ChangeSet-created file")

    (workspace / "src/main.cpp").write_text("// existing code\n" * 40000, encoding="utf-8")
    baseline = changes.inspect_workspace(workspace)
    stage = changes.create_stage()
    (stage / "src").mkdir()
    (stage / "src/main.cpp").write_text("void setup() { /* secure_boot disabled */ }\n", encoding="utf-8")
    change = changes.create_from_stage(
        provider_id="review", workspace_root=workspace, stage_root=stage,
        baseline=baseline, authorized_paths=["src/main.cpp"],
    )
    assessment = RiskAssessmentService().assess(change, explicit_edit_authorized=True)
    assert change.changed_files[0].diff_preview is None
    assert assessment.level == "low" and assessment.scoped_auto_apply_allowed
    record("unavailable preview receives low-risk auto-apply")

    changes.apply(change.change_set_id)
    (stage / "__undo__/src/main.cpp").write_text("corrupted backup\n", encoding="utf-8")
    changes.undo(change.change_set_id)
    assert (workspace / "src/main.cpp").read_text(encoding="utf-8") == "corrupted backup\n"
    record("undo accepts corrupted backup bytes")

    ini = "[env:review]\nplatform=espressif32\nboard=esp32dev\nframework=arduino\n"
    policy = BUILD_REPAIR_PROFILE.policy()
    for name, content in (
        ("platformio.ini", ini + "extra_scripts=pre:hook.py\n"),
        ("hook.py", "from pathlib import Path\n"),
    ):
        policy.validate_content(name, content)
        policy.resolve_path(workspace, name, must_exist=False)
        (workspace / name).write_text(content, encoding="utf-8")
    platformio = PlatformIOService(SubprocessManager())
    platformio.validate_project(workspace)
    record("agent file policy and project validation accept a build hook")

    (workspace / "platformio.ini").write_text(ini + "targets=upload\n", encoding="utf-8")
    platformio.validate_project(workspace)
    argv = FirmwareBuilder._build_args(BuildConfig(workspace), workspace)
    assert "--target" not in argv and "-t" not in argv
    record("upload-default configuration accepted with no explicit build target")

    skill = workspace / ".forgex/skills/custom-review/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("# Custom review\n", encoding="utf-8")
    baseline = changes.inspect_workspace(workspace)
    stage = changes.create_stage()
    for relative in sorted(baseline.files):
        target = stage / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(workspace / relative, target)
    assert any(item.name == "custom-review" for item in SkillRegistry(workspace).discover())
    assert not any(item.name == "custom-review" for item in SkillRegistry(stage).discover())
    record("autonomous staging loses project-local skills")

    library = workspace / ".pio/libdeps/demo/lib"
    library.mkdir(parents=True)
    for index in range(81):
        (library / f"a{index:03}.cpp").write_text("// dependency\n", encoding="utf-8")
    (workspace / "src/main.cpp").write_text(
        "#define LED_PIN 2\nvoid setup(){pinMode(LED_PIN, OUTPUT);}\n", encoding="utf-8",
    )
    graph = inspect_embedded_project(workspace)
    assert not any(pin.symbol == "LED_PIN" for pin in graph.pins)
    record("dependency inventory crowds application source out of hardware analysis")


if __name__ == "__main__":
    with TemporaryDirectory(prefix="forgex-review-") as temporary:
        asyncio.run(reproduce(Path(temporary)))
