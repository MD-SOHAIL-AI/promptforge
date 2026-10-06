import json

from backend.agent_runtime.esp32_tools import local_tool
from backend.agent_runtime.tool_contracts import ToolName
from backend.agent_runtime.tool_executor import ForgeXToolExecutor
from backend.agent_runtime.tool_policy import product_agent_policy


def test_symbol_inspection_marks_compiled_sources_without_exposing_commands(tmp_path):
    (tmp_path / "main.cpp").write_text("int sensorValue = 1;\n", encoding="utf-8")
    (tmp_path / "compile_commands.json").write_text(json.dumps([
        {"directory": str(tmp_path), "file": "main.cpp",
         "command": "secret-compiler-flag sensorValue"},
        {"directory": str(tmp_path), "file": "../outside.cpp", "command": "ignored"},
    ]), encoding="utf-8")
    executor = ForgeXToolExecutor(write_root=tmp_path, policy=product_agent_policy())
    result = local_tool(executor, ToolName.INSPECT_SYMBOLS, {"symbol": "sensorValue"})
    assert result["compile_database_present"] is True
    assert result["matches"][0]["compiled"] is True
    assert "secret-compiler-flag" not in json.dumps(result)
    assert result["lexical_only"] is True
