"""Audit-only, synthetic data and fake drivers. No product edits or devices."""
import asyncio
import json
import sys
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backend.agent_runtime.context_assembler import ContextAssembler
from backend.changes.models import WorkspaceSnapshot
from backend.services.terminal_service import TerminalService, TerminalSession
from backend.security.secret_scanner import has_high_confidence_secret
from backend.model_router.models import ModelRequest
from backend.model_router.providers.opencode import OpenCodeProvider
from backend.contracts.transport import TransportPolicy

OUT = Path(__file__).parent
fixture = OUT / 'probe-fixtures' / 'context-secret'
fixture.mkdir(parents=True, exist_ok=True)
marker = 'sk-' + 'SYNTHETIC_AUDIT_ONLY_' + 'a' * 64
(fixture / 'AGENTS.md').write_text('Synthetic instruction fixture\nOPENAI_API_KEY=' + marker, encoding='utf-8')
context = ContextAssembler(fixture).assemble(task='Inspect', snapshot=WorkspaceSnapshot(str(fixture), {}))

class StuckDriver:
    process = object()
    capability = 'pipe'
    output_streams = ()
    returncode = None
    killed = False
    async def terminate(self):
        pass
    async def kill(self):
        self.killed = True
    async def wait(self):
        await asyncio.Event().wait()

async def terminal_probe():
    driver = StuckDriver()
    session = TerminalSession('audit-fake-terminal', 'fake', str(fixture), driver.process, deque(), driver=driver)
    try:
        await asyncio.wait_for(TerminalService()._stop_session(session), timeout=2.3)
        return {'timed_out_after_kill': False, 'kill_called': driver.killed}
    except asyncio.TimeoutError:
        return {'timed_out_after_kill': True, 'kill_called': driver.killed, 'session_closed': session.closed}

async def worker_write_probe():
    class StuckInput:
        draining = False
        def write(self, data):
            pass
        async def drain(self):
            self.draining = True
            await asyncio.Event().wait()
        def close(self):
            pass
    class EmptyError:
        async def read(self, size):
            return b''
    class FakeProcess:
        stdin = StuckInput()
        stderr = EmptyError()
        returncode = None
        killed = False
        def kill(self):
            self.killed = True
            self.returncode = 0
        async def wait(self):
            return 0
    process = FakeProcess()
    async def fake_spawn(*args, **kwargs):
        return process
    original = asyncio.create_subprocess_exec
    asyncio.create_subprocess_exec = fake_spawn
    provider = OpenCodeProvider(object(), 'openai')
    provider._configured_health = lambda: None
    provider._payload = lambda request: ({'prompt':'synthetic'}, TransportPolicy(first_event_seconds=.1,idle_seconds=.1,total_seconds=.1,write_seconds=.05))
    generator = provider.generate_event_stream(ModelRequest(prompt='synthetic', provider_id='openai',model_id='synthetic'))
    try:
        await asyncio.wait_for(generator.__anext__(), timeout=.3)
        return {'outer_timeout_required':False}
    except asyncio.TimeoutError:
        return {'outer_timeout_required':True,'stdin_drain_entered':process.stdin.draining,
                'configured_total_seconds':.1,'external_cancel_seconds':.3,'child_killed_on_cancel':process.killed}
    finally:
        await generator.aclose()
        asyncio.create_subprocess_exec = original

result = {'context_instruction_secret_forwarded': marker in context.render(),
          'synthetic_key_matches_normal_secret_gate': has_high_confidence_secret(marker),
          'terminal_fault_injection': asyncio.run(terminal_probe()),
          'model_worker_stdin_fault_injection': asyncio.run(worker_write_probe())}
(OUT / 'additional-probes.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
print(json.dumps(result, indent=2))
