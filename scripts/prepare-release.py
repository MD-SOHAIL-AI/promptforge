#!/usr/bin/env python3
"""Stage the Windows x64 desktop runtime; never include developer state."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PYTHON_VERSION = "3.13.15"
PYTHON_ZIP = f"python-{PYTHON_VERSION}-embed-amd64.zip"
PYTHON_SHA256 = "d1f04d990aee1253d8569e8e5104e30fa9f5fa830899f14843448872d936a2cf"
IGNORED = {"__pycache__", ".pytest_cache", ".env", ".git", ".gitkeep", ".promptforge", ".forgex", ".venv"}


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def copy_tree(source, destination):
    def ignore(directory, names):
        for name in names:
            candidate = Path(directory) / name
            info = candidate.lstat()
            if candidate.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ValueError(f"RELEASE_LINK_UNSAFE: {candidate}")
        return [name for name in names if name in IGNORED or name.endswith((".pyc", ".log", ".pfx", ".pem")) or name.startswith(".env.")]
    shutil.copytree(source, destination, dirs_exist_ok=True, ignore=ignore)


def run(args, **kwargs):
    subprocess.run([str(arg) for arg in args], cwd=ROOT, check=True, timeout=1200, **kwargs)


def prepare_upload_tool_wheel(pip_python, lock, stage):
    """Validate the pinned esptool source, then bind its pure Python wheel hash.

    PyPI distributes esptool as source. Every other runtime dependency still
    uses the existing binary-only hashed installation path.
    """
    text = Path(lock).read_text(encoding="utf-8")
    pattern = r"(?m)^esptool==[^\n]+(?:\n[ \t]+--hash=[^\n]+)*"
    matches = list(re.finditer(pattern, text))
    if len(matches) != 1 or "--hash=sha256:" not in matches[0].group():
        raise ValueError("RELEASE_ESPTOOL_SOURCE_LOCK_REQUIRED")
    source = Path(stage) / "esptool-source.lock"
    source.write_text(matches[0].group() + "\n", encoding="utf-8")
    wheels = Path(stage) / "upload-tool-wheels"
    wheels.mkdir()
    run([pip_python, "-m", "pip", "wheel", "--no-deps", "--no-build-isolation", "--no-cache-dir",
         "--require-hashes", "-r", source, "--wheel-dir", wheels])
    built = list(wheels.glob("esptool-*-py3-none-any.whl"))
    if len(built) != 1:
        raise ValueError("RELEASE_ESPTOOL_WHEEL_INVALID")
    expected = matches[0].group().splitlines()[0].split("==", 1)[1].split()[0]
    if built[0].name != f"esptool-{expected}-py3-none-any.whl":
        raise ValueError("RELEASE_ESPTOOL_WHEEL_VERSION_MISMATCH")
    replacement = f"esptool=={expected} --hash=sha256:{digest(built[0])}"
    resolved = Path(stage) / "requirements-runtime-wheel.lock"
    resolved.write_text(text[:matches[0].start()] + replacement + text[matches[0].end():], encoding="utf-8")
    return resolved, wheels


def prepare(pip_python):
    if sys.platform != "win32":
        raise ValueError("Release staging requires Windows x64")
    # pip's dependency-marker evaluation uses its host interpreter even with
    # --python-version. Require the target minor version for the hashed lock.
    probe = subprocess.run([str(pip_python), "-c", "import sys; print('.'.join(map(str,sys.version_info[:2])))"],
                           capture_output=True, text=True, check=True, timeout=10)
    if probe.stdout.strip() != "3.13":
        raise ValueError("RELEASE_PIP_PYTHON_313_REQUIRED: select --pip-python or FORGEX_RELEASE_PYTHON")
    standalone = ROOT / "frontend/.next/standalone"
    if not standalone.is_dir() or not (ROOT / "dist/harness/server.js").is_file():
        raise ValueError("Build frontend, harness and Electron before staging")
    lock = ROOT / "requirements-runtime.lock"
    if not lock.is_file():
        raise ValueError("Generate the hashed requirements-runtime.lock before staging")
    stage = ROOT / ".forgex/release-staging" / uuid.uuid4().hex
    stage.mkdir(parents=True)
    application = stage / "forgex"
    application.mkdir()
    copy_tree(ROOT / "backend", application / "backend")
    copy_tree(ROOT / "dist/harness", application / "dist/harness")
    if not (ROOT / "dist/model-router/worker.cjs").is_file():
        raise ValueError("Build the OpenCode model engine before staging: npm run build:model-router")
    copy_tree(ROOT / "dist/model-router", application / "dist/model-router")
    copy_tree(ROOT / "third_party/opencode", application / "third_party/opencode")
    copy_tree(standalone, application)
    frontend = application / "frontend"
    if not (frontend / "server.js").is_file():
        raise ValueError("The standalone frontend must contain frontend/server.js")
    copy_tree(ROOT / "frontend/.next/static", frontend / ".next/static")
    if (ROOT / "frontend/public").is_dir():
        copy_tree(ROOT / "frontend/public", frontend / "public")
    cache = ROOT / ".forgex/release-cache"
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / PYTHON_ZIP
    if not archive.exists():
        url = f"https://www.python.org/ftp/python/{PYTHON_VERSION}/{PYTHON_ZIP}"
        with urllib.request.urlopen(url, timeout=60) as response, archive.open("xb") as output:
            shutil.copyfileobj(response, output)
    if digest(archive) != PYTHON_SHA256:
        raise ValueError("PYTHON_ARCHIVE_CHECKSUM_INVALID")
    python = application / "python"
    python.mkdir()
    with zipfile.ZipFile(archive) as zipped:
        for name in zipped.namelist():
            if Path(name).is_absolute() or ".." in Path(name).parts:
                raise ValueError("PYTHON_ARCHIVE_PATH_INVALID")
        zipped.extractall(python)
    (python / "python313._pth").write_text("python313.zip\n.\nLib/site-packages\n..\nimport site\n", encoding="utf-8")
    packages = python / "Lib/site-packages"
    resolved_lock, upload_wheels = prepare_upload_tool_wheel(pip_python, lock, stage)
    run([pip_python, "-m", "pip", "install", "--disable-pip-version-check", "--no-compile",
         "--target", packages, "--platform", "win_amd64", "--python-version", "3.13",
         "--implementation", "cp", "--abi", "cp313", "--only-binary=:all:",
         "--find-links", upload_wheels, "--require-hashes", "-r", resolved_lock])
    (application / "forgex-pio.py").write_text("from platformio.__main__ import main\nmain()\n", encoding="utf-8")
    runtime_python = python / "python.exe"
    environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "FORGEX_ROOT": str(application),
                   "FORGEX_DATA_DIR": str(stage / "smoke-data"), "FORGEX_LEGACY_DATA_DIR": "",
                   "FORGEX_LIVE_LOG_PATH": str(stage / "smoke-data/runtime.jsonl")}
    run([runtime_python, "-B", "-c", "import fastapi,uvicorn,winpty,serial,platformio,esptool; import backend.api.app; print('Bundled runtime imports passed')"], env=environment)
    run([runtime_python, "-B", application / "forgex-pio.py", "--version"], env={**environment, "PLATFORMIO_CORE_DIR": str(stage / "pio-smoke")})
    shutil.copyfile(lock, application / lock.name)
    shutil.copyfile(resolved_lock, application / resolved_lock.name)
    for name, prefix in (("desktop", []), ("frontend", ["--prefix", "frontend"])):
        with (application / f"sbom-{name}.json").open("wb") as output:
            run(["npm.cmd", *prefix, "sbom", "--sbom-format=cyclonedx"], stdout=output)
    # Record Python distributions without importing them or exposing local paths.
    from email.parser import Parser
    components = []
    for metadata in sorted(packages.glob("*.dist-info/METADATA")):
        parsed = Parser().parsestr(metadata.read_text(encoding="utf-8"))
        components.append({"type": "library", "name": parsed["Name"], "version": parsed["Version"]})
    (application / "sbom-python.json").write_text(json.dumps({"bomFormat": "CycloneDX", "specVersion": "1.6", "version": 1, "components": components}, indent=2), encoding="utf-8")
    manifest = {"schemaVersion": 1, "platform": "win32-x64", "pythonVersion": PYTHON_VERSION,
                "pythonArchiveSha256": PYTHON_SHA256, "files": {}}
    sys.path.insert(0, str(ROOT))
    from backend.runtime_contract import compute_backend_source_fingerprint
    manifest["backendSourceFingerprint"] = compute_backend_source_fingerprint(application)
    manifest["developmentSourceFingerprint"] = compute_backend_source_fingerprint(ROOT)
    manifest["appVersion"] = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["version"]
    for file in sorted(application.rglob("*")):
        if file.is_file():
            manifest["files"][file.relative_to(application).as_posix()] = digest(file)
    (application / "release-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    pointer = ROOT / ".forgex/release-stage.json"
    pointer.write_text(json.dumps({"stage": str(stage)}), encoding="utf-8")
    print(f"Staged desktop runtime: {stage}")
    return stage


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--pip-python", default=os.environ.get("FORGEX_RELEASE_PYTHON", sys.executable))
    prepare(parser.parse_args().pip_python)
