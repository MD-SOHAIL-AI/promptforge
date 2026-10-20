"""Repair only compiler-confirmed duplicate pin declarations with equal values."""
import pytest

from backend.agent_runtime.firmware_repair import repair_verified_cpp_build_errors


@pytest.mark.parametrize("member_value,expected", [("32", True), ("33", False)])
def test_compiler_proven_duplicate_private_pin_constant(tmp_path, member_value, expected):
    (tmp_path / "include").mkdir()
    (tmp_path / "include/Config.h").write_text("#pragma once\n#define BUTTON_PIN 32\n")
    target = tmp_path / "include/ButtonManager.h"
    source = "class ButtonManager {\nprivate:\n  static constexpr uint8_t BUTTON_PIN = " + member_value + ";\n};\n"
    target.write_text(source)
    build = {"success": False, "diagnostics":
        "include/Config.h:2:20: error: expected unqualified-id before numeric constant\n"
        " #define BUTTON_PIN 32\n                    ^~\n"
        "include/ButtonManager.h:3:30: note: in expansion of macro 'BUTTON_PIN'\n"
        "  static constexpr uint8_t BUTTON_PIN = 32;\n"}
    result = repair_verified_cpp_build_errors(tmp_path, build)
    assert bool(result) == expected
    assert (tmp_path / "include/Config.h").read_text() == "#pragma once\n#define BUTTON_PIN 32\n"
    if expected:
        assert "BUTTON_PIN" not in target.read_text()
        assert result.changed_paths == ("include/ButtonManager.h",)
    else:
        assert target.read_text() == source


@pytest.mark.parametrize("kind", ["public", "stale", "outside"])
def test_pin_macro_repair_rejects_unproven_or_external_edits(tmp_path, kind):
    (tmp_path / "Config.h").write_text("#define BUTTON_PIN 32\n")
    target = tmp_path / "Button.h"
    source = "class Button {\n" + ("public" if kind == "public" else "private") + ":\n  static constexpr uint8_t BUTTON_PIN = 32;\n};\n"
    target.write_text(source)
    path = "../Button.h" if kind == "outside" else "Button.h"
    line = 2 if kind == "stale" else 3
    result = repair_verified_cpp_build_errors(tmp_path, {"success": False, "diagnostics":
        "Config.h:1:20: error: expected unqualified-id before numeric constant\n"
        f"{path}:{line}:30: note: in expansion of macro 'BUTTON_PIN'\n"})
    assert result is None and target.read_text() == source
