import asyncio
import hashlib

import pytest

from backend.agent_runtime.build_readiness import local_header_readiness
from backend.contracts.progress import TaskExecution, LinkerDiagnostic, ModelQualification
from backend.model_router.qualification import model_qualification


def record():
    return {"provider_id": "provider", "model_id": "model", "source_fingerprint": "source",
        "endpoint_sha256": hashlib.sha256(b"https://api.example/v1").hexdigest(), "reviewed": True,
        "critical_passed": True, "generation_passes": 86, "maintenance_passes": 57,
        "generation_attempts": 90, "maintenance_attempts": 60, "target": "esp32dev", "framework": "arduino",
        "execution_profile": "standard", "checked_at": "2026-10-18T00:00:00Z"}


def test_qualification_is_bound_to_runtime_model_endpoint_and_profile():
    metadata = {"models": [record()]}
    qualified = model_qualification("provider", "model", "https://api.example/v1/", records=metadata, fingerprint="source")
    assert qualified["status"] == "tested"
    assert qualified["scopes"][0]["execution_profile"] == "standard"
    assert ModelQualification.model_validate(qualified)
    for provider, model, endpoint, source in (("other", "model", "https://api.example/v1", "source"),
            ("provider", "other", "https://api.example/v1", "source"),
            ("provider", "model", "https://proxy.example/v1", "source"),
            ("provider", "model", "https://api.example/v1", "stale")):
        assert model_qualification(provider, model, endpoint, records=metadata, fingerprint=source)["status"] == "untested"


@pytest.mark.parametrize("change", [{"reviewed": "true"}, {"critical_passed": False},
    {"generation_passes": 85}, {"maintenance_passes": 56}, {"generation_attempts": 89}, {"execution_profile": "unlimited"}])
def test_incomplete_or_loosely_typed_qualification_never_gets_a_tested_label(change):
    metadata = {"models": [{**record(), **change}]}
    assert model_qualification("provider", "model", "https://api.example/v1", records=metadata, fingerprint="source")["status"] == "untested"


def test_partial_implementation_is_advisory_and_different_filename_can_supply_definition():
    files = {"include/Device.h": "class Device { public: void init(); void update(); };",
             "src/Device.cpp": "void Device::init() {}", "src/more.cpp": "// Device::update() {}"}
    missing = local_header_readiness(files, files.__getitem__)
    assert missing["ready"] and missing["missing_local_implementations"][0]["methods"] == ["update"]
    files["src/more.cpp"] = "void Device::update() {}"
    assert local_header_readiness(files, files.__getitem__)["missing_local_implementations"] == []
    files = {"include/Inline.h": "class Inline { public: void init() {} };\ntemplate<class T> class Item { void run(); };"}
    assert local_header_readiness(files, files.__getitem__)["missing_local_implementations"] == []


def test_custom_source_and_include_roots_are_used_for_readiness():
    files = {"firmware/main.cpp": '#include "sensors/item.h"', "headers/sensors/item.h": "// present"}
    assert local_header_readiness(files, files.__getitem__, source_directory="firmware", include_directory="headers")["ready"]


def test_dependency_resolution_keeps_candidate_interface_and_bounds_metadata_probes(tmp_path):
    from backend.services.platformio_service import PlatformIOService
    from types import SimpleNamespace
    service = PlatformIOService.__new__(PlatformIOService)
    service._executable = "pio"
    service._library_metadata_cache = {}
    calls = []
    async def search(query, *, limit):
        return ("owner/Sensor", "other/Sensor")
    async def run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(success=True, stdout_text="Version  Status\n-------  ------\n1.2.3    latest\n", stderr_text="")
    service.search_libraries = search
    service._run = run
    first = asyncio.run(service.resolve_library("Sensor"))
    second = asyncio.run(service.resolve_library("Sensor"))
    assert first["candidates"] == ["owner/Sensor", "other/Sensor"]
    assert first["installed"] is False and first["packages"][0]["metadata_verified"]
    assert len(calls) == 1 and second["packages"][0]["cached"] is True


def test_public_linker_and_work_contracts_accept_unknown_locations():
    assert LinkerDiagnostic(kind="undefined reference to", symbol="Device::init()", message="unresolved").path is None
    assert TaskExecution(phase="repair", next_action="Implement Device::init()").version == 1


@pytest.mark.parametrize("prompt", ["Run the local build and verify every requirement against current source.",
    "Run a firmware build.", "Execute the build."])
def test_build_execution_wording_requires_current_host_result_not_a_source_citation(tmp_path, prompt):
    from backend.agent_runtime.task_contract import create_task_contract, verify_requirement_evidence
    contract = create_task_contract(prompt, project_id="p", session_id="s", workspace=tmp_path, intent="generate")
    assert contract["requirements"][0]["evidence_kinds"] == ["build"]
    checks = [{"id": "req-1", "status": "implemented", "evidence": []}]
    assert not verify_requirement_evidence(contract, checks, tmp_path, "r")["references_valid"]
    build = {"success": True, "workspace_revision": "r", "build_id": "host-build"}
    result = verify_requirement_evidence(contract, checks, tmp_path, "r", execution_evidence={"build": build})
    assert result["references_valid"] and not result["valid"]
    assert not verify_requirement_evidence(contract, checks, tmp_path, "new", execution_evidence={"build": build})["references_valid"]


def test_saved_source_classification_is_upgraded_without_losing_requirement_identity(tmp_path):
    from backend.agent_runtime.task_contract import create_task_contract, repair_task_contract
    contract = create_task_contract("Run the local build.", project_id="p", session_id="s", workspace=tmp_path, intent="generate")
    contract["evidence_contract_version"] = 2
    contract["requirements"][0]["evidence_kinds"] = ["source"]
    upgraded = repair_task_contract(contract, workspace=tmp_path)
    assert upgraded["evidence_contract_version"] == 3
    assert upgraded["requirements"][0]["evidence_kinds"] == ["build"]
    assert upgraded["requirements"][0]["id"] == contract["requirements"][0]["id"]
    assert upgraded["requirements"][0]["stable_key"] == contract["requirements"][0]["stable_key"]
    assert upgraded["revision"] != contract["revision"]
    assert contract["requirements"][0]["evidence_kinds"] == ["source"]


@pytest.mark.parametrize("prompt", ["Explain how to run the local build.", "Document the firmware build command."])
def test_describing_builds_still_requires_documentation_without_execution(tmp_path, prompt):
    from backend.agent_runtime.task_contract import create_task_contract
    contract = create_task_contract(prompt, project_id="p", session_id="s", workspace=tmp_path, intent="generate")
    assert contract["requirements"][0]["evidence_kinds"] == ["documentation"]
