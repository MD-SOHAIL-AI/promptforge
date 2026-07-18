from __future__ import annotations

import json

import pytest

from backend.agent_runtime.api_agent_contracts import (
    API_CODING_AGENT_SCHEMA_VERSION,
    MAX_FILE_BYTES,
    MAX_FILES,
    MAX_TOTAL_BYTES,
    ApiCodingAgentContractError,
    parse_api_coding_agent_response,
)
from backend.agent_runtime.api_coding_agent_fake import FakeApiCodingAgentProvider


def proposal(**overrides: object) -> str:
    value: dict[str, object] = {
        "schema_version": API_CODING_AGENT_SCHEMA_VERSION,
        "summary": "Create a project.",
        "files": [{"path": "src/main.cpp", "action": "create_or_update", "content": "int main() {}\n"}],
        "commands_suggested": [{"command": "pio run", "reason": "Build the project"}],
        "risks": ["Board configuration may vary."],
        "next_steps": ["Review the files.", "Run build."],
    }
    value.update(overrides)
    return json.dumps(value)


def test_valid_v1_proposal_is_preserved_and_commands_remain_data() -> None:
    parsed = parse_api_coding_agent_response(proposal())

    assert parsed.schema_version == API_CODING_AGENT_SCHEMA_VERSION
    assert parsed.summary == "Create a project."
    assert parsed.files[0].path == "src/main.cpp"
    assert parsed.files[0].action == "create_or_update"
    assert parsed.files[0].content == "int main() {}\n"
    assert parsed.commands_suggested[0].command == "pio run"
    assert parsed.commands_suggested[0].reason == "Build the project"
    assert parsed.risks == ("Board configuration may vary.",)
    assert parsed.next_steps == ("Review the files.", "Run build.")


def test_invalid_json_raises_typed_error() -> None:
    with pytest.raises(ApiCodingAgentContractError, match="not valid JSON"):
        parse_api_coding_agent_response("{not-json")


def test_markdown_wrapped_json_is_rejected() -> None:
    with pytest.raises(ApiCodingAgentContractError, match="not valid JSON"):
        parse_api_coding_agent_response(f"```json\n{proposal()}\n```")


def test_unknown_schema_version_is_rejected() -> None:
    with pytest.raises(ApiCodingAgentContractError, match="unsupported schema_version"):
        parse_api_coding_agent_response(proposal(schema_version="forgex.api_coding_agent.v2"))


@pytest.mark.parametrize(
    ("field", "message"),
    [
        ("schema_version", "missing required field: schema_version"),
        ("summary", "missing required field: summary"),
        ("files", "missing required field: files"),
    ],
)
def test_missing_required_top_level_fields_are_rejected(field: str, message: str) -> None:
    value = json.loads(proposal())
    del value[field]
    with pytest.raises(ApiCodingAgentContractError, match=message):
        parse_api_coding_agent_response(json.dumps(value))


def test_missing_file_path_is_rejected() -> None:
    files = [{"action": "create_or_update", "content": "text"}]
    with pytest.raises(ApiCodingAgentContractError, match=r"files\[0\]\.path is required"):
        parse_api_coding_agent_response(proposal(files=files))


def test_missing_create_content_is_rejected() -> None:
    files = [{"path": "README.md", "action": "create_or_update"}]
    with pytest.raises(ApiCodingAgentContractError, match="content is required"):
        parse_api_coding_agent_response(proposal(files=files))


def test_empty_files_rejected_by_default_and_can_be_explicitly_allowed() -> None:
    with pytest.raises(ApiCodingAgentContractError, match="at least one operation"):
        parse_api_coding_agent_response(proposal(files=[]))
    assert parse_api_coding_agent_response(proposal(files=[]), allow_empty=True).files == ()


@pytest.mark.parametrize(
    "path",
    [
        "../evil.txt",
        "/path/to/file",
        r"C:\temp\evil.txt",
        ".git/config",
        ".pio/build/output.bin",
        ".promptforge/state.json",
        ".env",
        "secrets.pem",
        "id_rsa",
        "src/ｍain.cpp",
    ],
)
def test_unsafe_and_protected_paths_are_rejected(path: str) -> None:
    files = [{"path": path, "action": "create_or_update", "content": "text"}]
    with pytest.raises(ApiCodingAgentContractError, match=r"files\[0\]\.path"):
        parse_api_coding_agent_response(proposal(files=files))


def test_duplicate_paths_are_rejected_case_insensitively() -> None:
    files = [
        {"path": "src/main.cpp", "action": "create_or_update", "content": "one"},
        {"path": "SRC/MAIN.CPP", "action": "create_or_update", "content": "two"},
    ]
    with pytest.raises(ApiCodingAgentContractError, match="duplicates another file path"):
        parse_api_coding_agent_response(proposal(files=files))


def test_binary_looking_content_is_rejected() -> None:
    files = [{"path": "asset.txt", "action": "create_or_update", "content": "text\x00data"}]
    with pytest.raises(ApiCodingAgentContractError, match="appears to be binary"):
        parse_api_coding_agent_response(proposal(files=files))


def test_delete_is_disabled_by_default_and_requires_explicit_option() -> None:
    raw = proposal(files=[{"path": "obsolete.txt", "action": "delete"}])
    with pytest.raises(ApiCodingAgentContractError, match="delete is disabled"):
        parse_api_coding_agent_response(raw)

    parsed = parse_api_coding_agent_response(raw, allow_delete=True)
    assert parsed.files[0].action == "delete"
    assert parsed.files[0].content is None


def test_file_larger_than_limit_is_rejected() -> None:
    files = [{"path": "large.txt", "action": "create_or_update", "content": "x" * (MAX_FILE_BYTES + 1)}]
    with pytest.raises(ApiCodingAgentContractError, match="exceeds max_file_bytes"):
        parse_api_coding_agent_response(proposal(files=files))


def test_total_content_larger_than_limit_is_rejected() -> None:
    per_file = MAX_FILE_BYTES
    file_count = (MAX_TOTAL_BYTES // per_file) + 1
    files = [
        {"path": f"src/file_{index}.txt", "action": "create_or_update", "content": "x" * per_file}
        for index in range(file_count)
    ]
    with pytest.raises(ApiCodingAgentContractError, match="exceeds max_total_bytes"):
        parse_api_coding_agent_response(proposal(files=files))


def test_too_many_files_are_rejected() -> None:
    files = [
        {"path": f"src/file_{index}.txt", "action": "create_or_update", "content": "x"}
        for index in range(MAX_FILES + 1)
    ]
    with pytest.raises(ApiCodingAgentContractError, match="exceeds max_files"):
        parse_api_coding_agent_response(proposal(files=files))


@pytest.mark.parametrize("item", [{"command": "", "reason": "why"}, {"command": "pio run", "reason": ""}])
def test_empty_command_suggestions_are_rejected(item: dict[str, str]) -> None:
    with pytest.raises(ApiCodingAgentContractError, match="must be a non-empty string"):
        parse_api_coding_agent_response(proposal(commands_suggested=[item]))


def test_unknown_fields_are_rejected() -> None:
    with pytest.raises(ApiCodingAgentContractError, match="unknown top-level fields"):
        parse_api_coding_agent_response(proposal(unexpected=True))


def test_fake_blink_output_is_valid() -> None:
    parsed = parse_api_coding_agent_response(FakeApiCodingAgentProvider().generate("make an ESP32 blink project"))
    assert [item.path for item in parsed.files] == ["platformio.ini", "src/main.cpp"]


@pytest.mark.parametrize(
    ("prompt", "message"),
    [
        ("unsafe path", r"files\[0\]\.path"),
        ("protected path", r"files\[0\]\.path"),
        ("invalid json", "not valid JSON"),
        ("oversized", "exceeds max_file_bytes"),
    ],
)
def test_fake_invalid_modes_are_rejected(prompt: str, message: str) -> None:
    raw = FakeApiCodingAgentProvider().generate(prompt)
    with pytest.raises(ApiCodingAgentContractError, match=message):
        parse_api_coding_agent_response(raw)
