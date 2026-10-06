"""Compile a host-owned ESP32 fixture and verify its complete sealed upload set.

This performs no flash or serial operation and does not claim device behavior.
Use the bundled Python interpreter to exercise the packaged build service.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import re
import sys
import tempfile


async def smoke(bundle: Path, output: Path, core: Path) -> None:
    bundle = bundle.resolve(strict=True)
    output.mkdir(parents=True, exist_ok=True)
    output = output.resolve(strict=True)
    project = Path(tempfile.mkdtemp(prefix="esp32-build-", dir=output))
    platform = json.loads((core / "platforms/espressif32/platform.json").read_text(encoding="utf-8"))
    version = platform["version"]
    if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("ESP32_SMOKE_INSTALLED_PLATFORM_VERSION_INVALID")
    (project / "src").mkdir()
    (project / "platformio.ini").write_text(
        f"[esp32_base]\nplatform=espressif32@{version}\nboard=esp32dev\nframework=arduino\n"
        "board_build.partitions=partitions.csv\n[env:esp32dev]\nextends=esp32_base\n",
        encoding="utf-8")
    (project / "partitions.csv").write_text(
        "# Name, Type, SubType, Offset, Size, Flags\n"
        "nvs,data,nvs,,0x5000,\notadata,data,ota,,0x2000,\n"
        "app0,app,ota_0,,1M,\napp1,app,ota_1,,1M,\nspiffs,data,spiffs,,0x1F0000,\n", encoding="utf-8")
    (project / "src/main.cpp").write_text(
        "#include <Arduino.h>\n"
        "void setup() { Serial.begin(115200); }\n"
        "void loop() {\n"
        "  static uint32_t last = 0;\n"
        "  const uint32_t now = millis();\n"
        "  if (uint32_t(now - last) >= 1000) { last = now; Serial.println(\"build-smoke\"); }\n"
        "}\n", encoding="utf-8")
    executable = bundle / "forgex-pio.py"
    if not executable.is_file():
        raise ValueError("ESP32_SMOKE_BUNDLED_PLATFORMIO_MISSING")
    os.environ.update(PYTHONDONTWRITEBYTECODE="1", FORGEX_ROOT=str(bundle), FORGEX_DATA_DIR=str(project / "data"),
                      FORGEX_LEGACY_DATA_DIR="", FORGEX_PLATFORMIO_EXECUTABLE=str(executable),
                      FORGEX_LIVE_LOG_PATH=str(project / "runtime.jsonl"))
    sys.path.insert(0, str(bundle))
    from backend.runtime.subprocess_mgr import SubprocessManager
    from backend.services.platformio_service import PlatformIOService
    from backend.services.firmware_manifest import lock_upload_set, verify_upload_set

    service = PlatformIOService(SubprocessManager(), executable=str(executable),
        env={"PLATFORMIO_CORE_DIR": str(core.resolve(strict=True))},
        timeout_s=300, artifact_store=project / "sealed-artifacts")
    result = await service.build(project, environment="esp32dev")
    process = result.process_result
    log = process.combined_text if process else result.message
    (project / "build.log").write_text(log, encoding="utf-8")
    if not result.success:
        raise RuntimeError(f"ESP32_SMOKE_BUILD_FAILED: inspect {project / 'build.log'}")
    assert result.metadata["partition_preflight"]["validation"] == "espressif_tool"
    assert result.metadata["partition_preflight"]["validated_environments"] == ["esp32dev"]
    if result.metadata.get("flash_ready") is not True:
        raise RuntimeError("ESP32_SMOKE_UPLOAD_NOT_READY: " + str(result.metadata.get("flash_readiness_error")))
    manifest = result.metadata["artifact_manifest"]
    images = verify_upload_set(manifest, store=project / "sealed-artifacts", chip="esp32", environment="esp32dev")
    assert {image["role"] for image in manifest["images"]} == {"bootloader", "partitions", "ota_data", "application"}
    with lock_upload_set(manifest):
        assert all(path.is_file() for _, path in images)
    report = {"schema_version": 1, "scope": "software_build_smoke", "passed": True,
              "platform_version": version, "platformio_version": result.toolchain_version,
              "partition_preflight": result.metadata["partition_preflight"],
              "firmware_sha256": next(image["sha256"] for image in manifest["images"] if image["role"] == "application"),
              "manifest": manifest,
              "hardware_verified": False, "model_qualification_performed": False,
              "build_log": str(project / "build.log")}
    path = output / "esp32-build-smoke.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("ESP32 compile and sealed upload set passed; no hardware operation performed: " + str(path))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--core-directory", type=Path, default=Path.home() / ".platformio")
    args = parser.parse_args()
    asyncio.run(smoke(args.bundle, args.output, args.core_directory))
