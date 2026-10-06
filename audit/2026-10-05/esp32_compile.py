"""Audit compiler probe, no device, serial, or provider requests."""
import asyncio
import json
import os
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
project = OUT / 'esp32-fixture'
project.joinpath('src').mkdir(parents=True, exist_ok=True)
core = Path.home() / '.platformio'
version = json.loads(core.joinpath('platforms/espressif32/platform.json').read_text())['version']
project.joinpath('platformio.ini').write_text(f'[env:esp32dev]\nplatform=espressif32@{version}\nboard=esp32dev\nframework=arduino\n', encoding='utf-8')
project.joinpath('src/main.cpp').write_text('#include <Arduino.h>\nvoid setup(){Serial.begin(115200);}\nvoid loop(){delay(1000);}\n', encoding='utf-8')
os.environ.update(FORGEX_ROOT=str(ROOT), FORGEX_DATA_DIR=str(OUT / 'compiler-data'), FORGEX_LEGACY_DATA_DIR='', FORGEX_LIVE_LOG_PATH=str(OUT / 'compiler-runtime.jsonl'))
from backend.runtime.subprocess_mgr import SubprocessManager
from backend.services.platformio_service import PlatformIOService

async def run():
    manager = SubprocessManager()
    service = PlatformIOService(manager, executable=str(ROOT / '.venv/Scripts/pio.exe'),
        env={'PLATFORMIO_CORE_DIR': str(core)}, timeout_s=300, artifact_store=OUT / 'compiler-artifacts')
    try:
        result = await service.build(project, environment='esp32dev')
        report = {'success': result.success, 'status': str(result.status), 'message': result.message,
            'platform_version': version, 'metadata': result.metadata, 'hardware_verified': False}
        (OUT / 'esp32-compile.json').write_text(json.dumps(report, indent=2, default=str), encoding='utf-8')
        process = result.process_result
        (OUT / 'esp32-compiler-output.log').write_text(process.combined_text if process else result.message, encoding='utf-8')
        print(json.dumps(report, indent=2, default=str))
    finally:
        await manager.kill_all()
asyncio.run(run())
