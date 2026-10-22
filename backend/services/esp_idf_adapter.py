"""Experimental native ESP-IDF adapter; no shell or implicit hardware access.

CMake is executable project content. Native builds require a host-supplied OS
sandbox and explicit experimental activation, rather than treating trust as one.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

from backend.runtime.result import BuildResult, ResultStatus
from backend.runtime.subprocess_mgr import ProcessConfig
from .firmware_manifest import seal_upload_set
from .project_profile import inspect_project_profile


class EspIdfAdapter:
    adapter_id = "esp-idf"
    qualification = "experimental"

    def __init__(self, *, idf_root, artifact_store, sandbox=None, enabled=False):
        self.idf_root = Path(idf_root).resolve()
        self.artifact_store = Path(artifact_store).resolve()
        self.sandbox = sandbox
        self.enabled = enabled is True

    def inspect(self, project):
        profile = inspect_project_profile(project)
        if profile.adapter != self.adapter_id:
            raise ValueError("ESP_IDF_PROJECT_REQUIRED")
        return profile

    async def build(self, project, *, trusted=False, timeout_s=180):
        root = Path(project).resolve(strict=True)
        self.inspect(root)
        if not self.enabled:
            raise ValueError("ESP_IDF_EXPERIMENTAL_DISABLED")
        if trusted is not True:
            raise ValueError("AGENT_PROJECT_TRUST_REQUIRED")
        if self.sandbox is None or not callable(getattr(self.sandbox, "run", None)):
            raise ValueError("ESP_IDF_SANDBOX_REQUIRED")
        script = self.idf_root / "tools/idf.py"
        if not script.is_file() or script.is_relative_to(root):
            raise ValueError("ESP_IDF_TOOLCHAIN_UNAVAILABLE")
        if not isinstance(timeout_s, (int, float)) or isinstance(timeout_s, bool) or not 1 <= timeout_s <= 900:
            raise ValueError("ESP_IDF_TIMEOUT_INVALID")
        result = await self.sandbox.run(ProcessConfig(args=[sys.executable, str(script), "-C", str(root),
            "-B", str(root / "build"), "build"], cwd=str(root), timeout_s=timeout_s,
            env={"IDF_PATH": str(self.idf_root)}), workspace_root=root, network=False, hardware=False)
        if not result.success:
            return BuildResult(success=False, status=ResultStatus.FAILED,
                message="Native ESP-IDF build failed.", process_result=result,
                metadata={"adapter": self.adapter_id, "qualification": self.qualification})
        manifest = self.seal_build(root)
        application = next(item for item in manifest["images"] if item["role"] == "application")
        return BuildResult(success=True, status=ResultStatus.SUCCESS, message="Experimental ESP-IDF build completed.",
            firmware_path=str(Path(manifest["artifact_root"]) / application["filename"]),
            build_size_bytes=application["size_bytes"], process_result=result, board=manifest["chip"],
            metadata={"adapter": self.adapter_id, "qualification": self.qualification, "artifact_manifest": manifest,
                      "artifact_environment": "esp-idf", "flash_ready": True})

    def seal_build(self, project):
        build = Path(project).resolve(strict=True) / "build"
        metadata = build / "flasher_args.json"
        if metadata.is_symlink() or metadata.stat().st_size > 128_000:
            raise ValueError("ESP_IDF_FLASH_METADATA_INVALID")
        value = json.loads(metadata.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or not isinstance(value.get("extra_esptool_args"), dict):
            raise ValueError("ESP_IDF_FLASH_METADATA_INVALID")
        chip = value["extra_esptool_args"].get("chip")
        files = value.get("flash_files")
        if not isinstance(files, dict) or not 3 <= len(files) <= 4:
            raise ValueError("ESP_IDF_UPLOAD_SET_INCOMPLETE")
        roles = {}
        for source, target in (("bootloader", "bootloader"), ("partition_table", "partitions"), ("app", "application"), ("otadata", "ota_data")):
            entry = value.get(source)
            if entry:
                if not isinstance(entry, dict) or not isinstance(entry.get("file"), str):
                    raise ValueError("ESP_IDF_FLASH_METADATA_INVALID")
                roles[entry["file"]] = target
        images = []
        for offset, name in files.items():
            if not isinstance(offset, str) or not isinstance(name, str) or name not in roles:
                raise ValueError("ESP_IDF_UPLOAD_IMAGE_UNSUPPORTED")
            path = build / name
            path.resolve(strict=True).relative_to(build.resolve(strict=True))
            if path.is_symlink() or any(parent.is_symlink() for parent in path.parents if parent.is_relative_to(build)):
                raise ValueError("ESP_IDF_UPLOAD_PATH_UNSAFE")
            images.append((roles[name], int(offset, 0), path))
        return seal_upload_set(self.artifact_store, chip=chip, environment="esp-idf", board=chip,
                               images=images, toolchain={"adapter": self.adapter_id, "qualification": self.qualification})

    @staticmethod
    def crash_artifact(elf, expected_sha256):
        path = Path(elf).resolve(strict=True)
        if path.suffix != ".elf" or hashlib.sha256(path.read_bytes()).hexdigest() != expected_sha256:
            raise ValueError("ESP_IDF_DEBUG_ARTIFACT_MISMATCH")
        return path
