"""Evidence assembly preserves provenance without declaring a release pass."""
import importlib.util
import json
from pathlib import Path

import pytest


def assembler():
    path = Path(__file__).resolve().parents[2] / "scripts/assemble-reliability-evidence.py"
    spec = importlib.util.spec_from_file_location("reliability_assembler", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_assembly_binds_files_and_verifier_encoding_without_attesting_a_pass(tmp_path):
    module = assembler()
    def save(name, data):
        path = tmp_path / name
        path.write_text(json.dumps(data), encoding="utf-8")
        return path
    installer = tmp_path / "fixture.exe"
    installer.write_bytes(b"unit test fixture; not a release installer")
    save("fixture.exe.evidence.json", {"resourceManifestSha256": "a" * 64})
    reports = {name: str(save(name + ".json", {"backend_source_fingerprint": "b" * 64}))
        for name in ("generation", "maintenance", "targets")}
    config = save("configuration.json", {"schema_version": 1, "models": [
        {"id": "fixture", "endpoint_sha256": "c" * 64, "reports": reports}],
        "encoder_regression": {"2": "é", "10": 1.0}})
    incidents = [save(f"incident-{i}.json", {"backend_source_fingerprint": "b" * 64}) for i in range(3)]
    result = module.assemble(config, installer, incidents)
    assert result["installer_sha256"] == module.digest(installer)
    assert result["resource_manifest_sha256"] == "a" * 64
    assert result["backend_source_fingerprint"] == "b" * 64
    assert len(result["config_sha256"]) == 64
    assert "passed" not in result and result["models"][0]["generation"] == {"backend_source_fingerprint": "b" * 64}
    save("incident-2.json", {"backend_source_fingerprint": "d" * 64})
    with pytest.raises(ValueError, match="FINGERPRINT_MISMATCH"):
        module.assemble(config, installer, incidents)


def test_incident_runner_checks_installer_and_packaged_files_before_any_model_call(tmp_path):
    import hashlib
    path = Path(__file__).resolve().parents[2] / "scripts/qualify-forgex-runtime.py"
    spec = importlib.util.spec_from_file_location("incident_qualifier", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    files = {}
    for relative in ("python/python.exe", "forgex-pio.py", "backend/runtime_contract.py"):
        file = runtime / relative
        file.parent.mkdir(exist_ok=True)
        file.write_bytes(b"unit test fixture only")
        files[relative] = hashlib.sha256(file.read_bytes()).hexdigest()
    manifest = runtime / "release-manifest.json"
    manifest.write_text(json.dumps({"files": files}))
    installer = tmp_path / "fixture.exe"
    installer.write_bytes(b"unit test installer fixture")
    sidecar = Path(str(installer) + ".evidence.json")
    sidecar.write_text(json.dumps({"sha256": hashlib.sha256(installer.read_bytes()).hexdigest(),
        "resourceManifestSha256": hashlib.sha256(manifest.read_bytes()).hexdigest()}))
    assert module.packaged_runtime(runtime, installer)[0] == runtime.resolve()
    (runtime / "backend/runtime_contract.py").write_bytes(b"changed after packaging")
    with pytest.raises(ValueError, match="PACKAGED_FILE_CHANGED"):
        module.packaged_runtime(runtime, installer)
    installer.write_bytes(b"changed installer")
    with pytest.raises(ValueError, match="INSTALLER_RUNTIME_MISMATCH"):
        module.packaged_runtime(runtime, installer)


def test_embedded_child_startup_disables_bytecode_without_environment_or_B_flag(tmp_path):
    import os
    import subprocess
    import sys
    path = Path(__file__).resolve().parents[2] / "scripts/prepare-release.py"
    spec = importlib.util.spec_from_file_location("release_prepare", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.configure_embedded_python(tmp_path)
    packages = tmp_path / "Lib/site-packages"
    (packages / "fixture_module.py").write_text("value = 7\n")
    environment = {key: value for key, value in os.environ.items() if key not in {"PYTHONDONTWRITEBYTECODE", "PYTHONPATH"}}
    subprocess.run([sys.executable, "-c", "import site,sys;site.addsitedir(sys.argv[1]);"
        "assert sys.dont_write_bytecode;import fixture_module;assert fixture_module.value == 7", str(packages)],
        env=environment, check=True, capture_output=True, timeout=20)
    assert not list(packages.rglob("*.pyc"))
