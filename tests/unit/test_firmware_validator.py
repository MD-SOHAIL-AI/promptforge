from dataclasses import dataclass
from datetime import datetime, timezone

import pytest

from backend.contracts.generated_project import GeneratedFile, GeneratedProject
from backend.validation.firmware_validator import (
    FirmwareValidationCategory,
    FirmwareValidationSeverity,
    FirmwareValidator,
)


VALID_INI = """\
[env:uno]
platform = atmelavr
board = uno
framework = arduino
"""

VALID_SOURCE = """\
#include <Arduino.h>
void setup() {}
void loop() {}
"""


def project(
    *,
    framework: str = "PlatformIO",
    ini: str = VALID_INI,
    source: str = VALID_SOURCE,
    extra_files: list[dict[str, str]] | None = None,
) -> dict[str, object]:
    files = [
        {"path": "platformio.ini", "content": ini},
        {"path": "src/main.cpp", "content": source},
    ]
    files.extend(extra_files or [])
    return {"framework": framework, "files": files}


def test_valid_platformio_project() -> None:
    result = FirmwareValidator().validate_project(project())

    assert result.valid is True
    assert result.issues == ()


@pytest.mark.parametrize("value", [None, 42, {"framework": "PlatformIO"}])
def test_malformed_project_becomes_structured_issue(value: object) -> None:
    result = FirmwareValidator().validate_project(value)

    assert result.has_errors() is True
    assert result.errors[0].rule_id == "PF-FW-000"


def test_mapping_of_paths_to_content_is_supported() -> None:
    result = FirmwareValidator().validate_project(
        {
            "framework": "PlatformIO",
            "files": {
                "platformio.ini": VALID_INI,
                "src/main.cpp": VALID_SOURCE,
            },
        }
    )

    assert result.valid is True


def test_generated_project_contract_is_supported() -> None:
    generated = GeneratedProject(
        project_id="project-1",
        project_name="project-one",
        target_board="Arduino Uno",
        framework="PlatformIO",
        files=(
            GeneratedFile("platformio.ini", VALID_INI, "ini"),
            GeneratedFile("src/main.cpp", VALID_SOURCE, "cpp"),
        ),
        created_at=datetime.now(timezone.utc),
    )

    assert FirmwareValidator().validate_project(generated).valid is True


@pytest.mark.parametrize("missing", ["platformio.ini", "src/main.cpp"])
def test_platformio_required_files(missing: str) -> None:
    value = project()
    value["files"] = [
        item for item in value["files"] if item["path"] != missing  # type: ignore[index]
    ]

    result = FirmwareValidator().validate_project(value)

    matching = [issue for issue in result.errors if issue.rule_id == "PF-FW-006"]
    assert len(matching) == 1
    assert missing in matching[0].message


def test_empty_project_and_empty_files_are_rejected() -> None:
    empty_project = FirmwareValidator().validate_structure(
        {"framework": "PlatformIO", "files": []}
    )
    empty_file = FirmwareValidator().validate_structure(
        project(source=" \n\t")
    )

    assert empty_project.errors[0].rule_id == "PF-FW-001"
    assert any(issue.rule_id == "PF-FW-005" for issue in empty_file.errors)


def test_duplicate_paths_are_case_insensitive() -> None:
    value = project(extra_files=[{"path": "SRC/Main.cpp", "content": "other"}])

    result = FirmwareValidator().validate_structure(value)

    assert any(issue.rule_id == "PF-FW-002" for issue in result.errors)


@pytest.mark.parametrize("path", ["../main.cpp", "/src/main.cpp", "src\\main.cpp", "src//x.cpp"])
def test_unsafe_project_paths_are_rejected(path: str) -> None:
    result = FirmwareValidator().validate_structure(
        project(extra_files=[{"path": path, "content": "int x;"}])
    )

    assert any(issue.rule_id == "PF-FW-003" for issue in result.errors)


@pytest.mark.parametrize("framework", ["Python", "UNKNOWN", "", None])
def test_unsupported_or_missing_framework(framework: object) -> None:
    value = project()
    value["framework"] = framework

    result = FirmwareValidator().validate_project(value)

    assert any(issue.rule_id in {"PF-FW-000", "PF-FW-004"} for issue in result.errors)


@pytest.mark.parametrize(
    ("source", "rule_id"),
    [
        ("void loop() {}", "PF-FW-008"),
        ("void setup() {}", "PF-FW-009"),
        ("// void setup() {}\nvoid loop() {}", "PF-FW-008"),
        ("/* void loop() {} */\nvoid setup() {}", "PF-FW-009"),
    ],
)
def test_missing_setup_and_loop_detection(source: str, rule_id: str) -> None:
    result = FirmwareValidator().validate_source_files(project(source=source))

    assert any(issue.rule_id == rule_id for issue in result.errors)


def test_function_declarations_without_definitions_do_not_pass() -> None:
    result = FirmwareValidator().validate_source_files(
        project(source="void setup();\nvoid loop();")
    )

    assert {issue.rule_id for issue in result.errors} == {"PF-FW-008", "PF-FW-009"}


def test_no_source_files_is_rejected() -> None:
    result = FirmwareValidator().validate_source_files(
        {
            "framework": "PlatformIO",
            "files": [{"path": "platformio.ini", "content": VALID_INI}],
        }
    )

    assert result.errors[0].rule_id == "PF-FW-007"


@pytest.mark.parametrize(
    "ini",
    [
        "[env:test\nboard=uno",
        "[env:test]\nboard=uno\nboard=esp32dev",
        "[env:test]\nboard=uno\n[env:test]\nboard=uno",
    ],
)
def test_malformed_and_duplicate_configuration(ini: str) -> None:
    result = FirmwareValidator().validate_platformio(project(ini=ini))

    assert result.errors[0].rule_id == "PF-FW-010"


def test_platformio_requires_an_environment() -> None:
    result = FirmwareValidator().validate_platformio(
        project(ini="[platformio]\ndefault_envs = uno")
    )

    assert any(issue.rule_id == "PF-FW-011" for issue in result.errors)


@pytest.mark.parametrize("option", ["platform", "board", "framework"])
def test_platformio_requires_complete_build_configuration(option: str) -> None:
    lines = [line for line in VALID_INI.splitlines() if not line.startswith(option)]

    result = FirmwareValidator().validate_platformio(project(ini="\n".join(lines)))

    assert any(
        issue.rule_id == "PF-FW-012" and option in issue.message
        for issue in result.errors
    )


def test_platformio_global_env_options_are_inherited() -> None:
    ini = """\
[env]
platform = atmelavr
framework = arduino

[env:uno]
board = uno
"""

    assert FirmwareValidator().validate_platformio(project(ini=ini)).valid is True


def test_platformio_environment_extends_parent() -> None:
    ini = """\
[env:base]
platform = espressif32
board = esp32dev
framework = arduino

[env:esp32]
extends = env:base
"""

    assert FirmwareValidator().validate_platformio(project(ini=ini)).valid is True


@pytest.mark.parametrize(
    ("default_envs", "rule_id"),
    [("missing", "PF-FW-025"), ("uno, uno", "PF-FW-025")],
)
def test_invalid_default_environment_selection(
    default_envs: str,
    rule_id: str,
) -> None:
    ini = f"[platformio]\ndefault_envs={default_envs}\n\n" + VALID_INI

    result = FirmwareValidator().validate_platformio(project(ini=ini))

    assert any(issue.rule_id == rule_id for issue in result.errors)


def test_undefined_parent_environment_is_rejected() -> None:
    ini = VALID_INI + "extends = env:missing\n"

    result = FirmwareValidator().validate_platformio(project(ini=ini))

    assert any(issue.rule_id == "PF-FW-026" for issue in result.errors)


def test_cyclic_environment_inheritance_is_rejected() -> None:
    ini = """\
[env:a]
extends=env:b
platform=atmelavr
board=uno
framework=arduino

[env:b]
extends=env:a
platform=atmelavr
board=uno
framework=arduino
"""

    result = FirmwareValidator().validate_platformio(project(ini=ini))

    assert any(issue.rule_id == "PF-FW-026" for issue in result.errors)


def test_non_arduino_platformio_framework_is_rejected() -> None:
    ini = VALID_INI.replace("framework = arduino", "framework = espidf")

    result = FirmwareValidator().validate_platformio(project(ini=ini))

    assert any(issue.rule_id == "PF-FW-013" for issue in result.errors)


@pytest.mark.parametrize(
    ("flag", "rule_id"),
    [
        ("-fpermissive", "PF-FW-014"),
        ("-w", "PF-FW-015"),
        ("-Wno-error", "PF-FW-015"),
        ("-fno-stack-protector", "PF-FW-016"),
        ("-Wl,--allow-multiple-definition", "PF-FW-017"),
        ("-DNDEBUG", "PF-FW-018"),
    ],
)
def test_dangerous_compile_flags(flag: str, rule_id: str) -> None:
    ini = VALID_INI + f"build_flags = {flag}\n"

    result = FirmwareValidator().validate_platformio(project(ini=ini))

    assert any(issue.rule_id == rule_id for issue in result.errors)


def test_normal_compile_flags_are_accepted() -> None:
    ini = VALID_INI + "build_flags = -Wall -Wextra -DLED_PIN=13\n"

    assert FirmwareValidator().validate_platformio(project(ini=ini)).valid is True


def test_supported_dependencies_are_accepted() -> None:
    ini = VALID_INI + """\
lib_deps =
    bblanchon/ArduinoJson @ ^7.0.0
    knolleary/PubSubClient @ ^2.8
"""

    assert FirmwareValidator().validate_dependencies(project(ini=ini)).valid is True


def test_unsupported_library_is_rejected() -> None:
    ini = VALID_INI + "lib_deps = unknown/UnsafeFirmwareLib @ 1.0.0\n"

    result = FirmwareValidator().validate_dependencies(project(ini=ini))

    assert result.errors[0].rule_id == "PF-FW-021"


def test_duplicate_dependency_is_rejected() -> None:
    ini = VALID_INI + """\
lib_deps =
    bblanchon/ArduinoJson @ ^7
    ArduinoJson @ ^6
"""

    result = FirmwareValidator().validate_dependencies(project(ini=ini))

    assert any(issue.rule_id == "PF-FW-020" for issue in result.errors)


def test_same_dependency_in_separate_environments_is_allowed() -> None:
    ini = """\
[env:a]
platform=atmelavr
board=uno
framework=arduino
lib_deps=bblanchon/ArduinoJson @ ^7

[env:b]
platform=atmelavr
board=uno
framework=arduino
lib_deps=bblanchon/ArduinoJson @ ^7
"""

    assert FirmwareValidator().validate_dependencies(project(ini=ini)).valid is True


def test_url_dependency_is_not_accepted_without_review() -> None:
    ini = VALID_INI + "lib_deps = https://example.com/library.git\n"

    result = FirmwareValidator().validate_dependencies(project(ini=ini))

    assert result.errors[0].rule_id == "PF-FW-019"


def test_library_policy_is_injectable() -> None:
    ini = VALID_INI + "lib_deps = acme/ReviewedDriver @ 1.0.0\n"
    validator = FirmwareValidator(supported_libraries=["ReviewedDriver"])

    assert validator.validate_dependencies(project(ini=ini)).valid is True


def test_arduino_project_structure_and_entry_points() -> None:
    value = {
        "framework": "Arduino",
        "files": [{"path": "blink.ino", "content": VALID_SOURCE}],
    }

    assert FirmwareValidator().validate_project(value).valid is True


def test_arduino_requires_exactly_one_sketch() -> None:
    value = {
        "framework": "Arduino",
        "files": [
            {"path": "one.ino", "content": VALID_SOURCE},
            {"path": "two.ino", "content": "int helper;"},
        ],
    }

    result = FirmwareValidator().validate_project(value)

    assert any(issue.rule_id == "PF-FW-022" for issue in result.errors)


def test_esp_idf_structure_and_entry_point() -> None:
    value = {
        "framework": "ESP-IDF",
        "files": [
            {"path": "CMakeLists.txt", "content": "project(app)"},
            {"path": "main/CMakeLists.txt", "content": "idf_component_register()"},
            {"path": "main/main.c", "content": "void app_main(void) {}"},
        ],
    }

    assert FirmwareValidator().validate_project(value).valid is True


def test_stm32cube_structure_and_entry_point() -> None:
    value = {
        "framework": "STM32Cube",
        "files": [
            {"path": "Core/Inc/main.h", "content": "#pragma once"},
            {"path": "Core/Src/main.c", "content": "int main(void) {}"},
        ],
    }

    assert FirmwareValidator().validate_project(value).valid is True


@dataclass(frozen=True, slots=True)
class FileMetadata:
    path: str
    content: str


@dataclass(frozen=True, slots=True)
class ProjectMetadata:
    framework: str
    files: tuple[FileMetadata, ...]


def test_slotted_metadata_objects_are_supported() -> None:
    value = ProjectMetadata(
        "PlatformIO",
        (
            FileMetadata("platformio.ini", VALID_INI),
            FileMetadata("src/main.cpp", VALID_SOURCE),
        ),
    )

    assert FirmwareValidator().validate_project(value).valid is True


def test_result_summary_is_deterministic() -> None:
    result = FirmwareValidator().validate_source_files(
        project(source="void setup() {}")
    )

    assert result.summary() == "INVALID: 1 issue(s) (info=0, warnings=0, errors=1)"
    assert result.is_valid is False
    assert result.errors[0].category is FirmwareValidationCategory.SOURCE
    assert result.errors[0].severity is FirmwareValidationSeverity.ERROR
