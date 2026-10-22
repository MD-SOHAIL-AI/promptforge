"""Run the real dependency preflight and compiler on the retained repair copy."""
import argparse
import asyncio
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from backend.runtime.subprocess_mgr import SubprocessManager
from backend.runtime.process_output import observe_output
from backend.services.platformio_service import PlatformIOService
from backend.utils.paths import PathManager

parser = argparse.ArgumentParser()
parser.add_argument("--attempt", type=int, default=1)
parser.add_argument("--skip-preflight", action="store_true")
parser.add_argument("--timeout", type=float, default=600)
parser.add_argument("--active", action="store_true")
args = parser.parse_args()
audit = Path(__file__).parent
context = json.loads((audit / "repair-context.json").read_text(encoding="utf-8"))
stage = Path(context["workspace_root"] if args.active else context["repair_stage"])


async def main():
    manager = SubprocessManager()
    artifact_store = (PathManager(ROOT).artifacts_directory() / "firmware-manifests"
                      if args.active else audit / "firmware-manifests")
    service = PlatformIOService(manager, artifact_store=artifact_store)
    try:
        if not args.skip_preflight:
            validation = await service.validate_dependencies(stage)
            (audit / "dependency-validation.json").write_text(json.dumps(validation, indent=2) + "\n", encoding="utf-8")
            print(json.dumps({"dependency_validation": validation}), flush=True)
            assert validation["valid"], validation
        output = audit / f"build-{args.attempt}.log"
        async def record(chunk):
            with output.open("a", encoding="utf-8") as stream:
                stream.write(chunk)
        with observe_output(record):
            result = await service.build(stage, environment="esp32dev", timeout_s=args.timeout)
        data = result.api_dict()
        (audit / f"build-{args.attempt}.json").write_text(json.dumps(data, indent=2, default=str) + "\n", encoding="utf-8")
        text = output.read_text(encoding="utf-8") if output.exists() else ""
        print(json.dumps({"success": result.success, "status": str(result.status),
            "errors": [line for line in text.splitlines() if re.search(r"(?:error:|Error:|FAILED)", line)][:24],
            "last_output": text.splitlines()[-12:], "evidence": str(output)}), flush=True)
        return result.success
    finally:
        await manager.kill_all()


if not asyncio.run(main()):
    sys.exit(1)
