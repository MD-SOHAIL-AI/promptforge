import asyncio,json
from pathlib import Path
import pytest
from backend.agent_runtime.agent_adapter import (AdapterFailure,AdapterReadiness,AgentAdapterDescriptor,AgentAdapterError,ApprovedBoundedContext,ContainedProposalAdapter,MAX_CONTEXT_FILES,disabled_cli_descriptors,deterministic_fake_adapter,structured_api_adapter,verified_template_adapter)
from backend.agent_runtime.api_agent_contracts import API_CODING_AGENT_SCHEMA_VERSION,ApiCodingAgentFileOperation,ApiCodingAgentProposal
from backend.bridges.diff_service import BridgeDiffService
from backend.bridges.sandbox_service import BridgeSandboxService

def env(tmp_path):
 workspace=tmp_path/"active";workspace.mkdir();(workspace/"main.txt").write_text("original\n");reviews=BridgeDiffService();sandboxes=BridgeSandboxService(tmp_path/"sandboxes");return workspace,reviews,sandboxes
def raw(path="proposal.txt",content="safe\n",commands=None):return json.dumps({"schema_version":API_CODING_AGENT_SCHEMA_VERSION,"summary":"proposal","files":[{"path":path,"action":"create_or_update","content":content}],"commands_suggested":commands or [],"risks":[],"next_steps":[]})

@pytest.mark.asyncio
async def test_fake_adapter_keeps_active_workspace_intact_and_forgex_derives_review(tmp_path):
 workspace,reviews,sandboxes=env(tmp_path);adapter=deterministic_fake_adapter(sandboxes=sandboxes,reviews=reviews);run=adapter.prepare_run(run_id="run",workspace=workspace,context=ApprovedBoundedContext("create smoke"));await adapter.execute_turn(run,timeout_seconds=1);result=adapter.collect_result(run)
 assert (workspace/"main.txt").read_text()=="original\n" and not (workspace/"FORGEX_AGENT_RUNTIME_SMOKE.txt").exists()
 assert result.changed_files==("FORGEX_AGENT_RUNTIME_SMOKE.txt",) and reviews.get_review(result.review_id).status=="pending"

def test_context_limits_secret_and_path_rejection():
 with pytest.raises(AgentAdapterError): ApprovedBoundedContext("x",{f"f{i}":"x" for i in range(MAX_CONTEXT_FILES+1)})
 with pytest.raises(AgentAdapterError): ApprovedBoundedContext("x",{"../escape":"x"})
 with pytest.raises(AgentAdapterError): ApprovedBoundedContext("x",{"safe":"token=raw"})
 with pytest.raises(AgentAdapterError): ApprovedBoundedContext("password=raw")

@pytest.mark.asyncio
async def test_cancellation_interrupts_inflight_turn(tmp_path):
 workspace,reviews,sandboxes=env(tmp_path)
 async def slow(turn): await asyncio.sleep(10);return raw()
 adapter=structured_api_adapter(sandboxes=sandboxes,reviews=reviews,source=slow);run=adapter.prepare_run(run_id="cancel",workspace=workspace,context=ApprovedBoundedContext("safe"));task=asyncio.create_task(adapter.execute_turn(run,timeout_seconds=20));await asyncio.sleep(.01);adapter.cancel(run)
 with pytest.raises(AgentAdapterError) as exc: await task
 assert exc.value.code is AdapterFailure.CANCELLED

@pytest.mark.asyncio
async def test_timeout_fails_closed(tmp_path):
 workspace,reviews,sandboxes=env(tmp_path)
 async def slow(turn): await asyncio.sleep(1);return raw()
 adapter=structured_api_adapter(sandboxes=sandboxes,reviews=reviews,source=slow);run=adapter.prepare_run(run_id="timeout",workspace=workspace,context=ApprovedBoundedContext("safe"))
 with pytest.raises(AgentAdapterError) as exc: await adapter.execute_turn(run,timeout_seconds=.01)
 assert exc.value.code is AdapterFailure.TIMED_OUT

@pytest.mark.asyncio
async def test_output_limit_and_unsafe_path_rejected(tmp_path):
 workspace,reviews,sandboxes=env(tmp_path)
 huge=ApiCodingAgentProposal(API_CODING_AGENT_SCHEMA_VERSION,"huge",tuple(ApiCodingAgentFileOperation(f"f{i}.txt","create_or_update","x"*(64*1024)) for i in range(9)),(),(),())
 adapter=structured_api_adapter(sandboxes=sandboxes,reviews=reviews,source=lambda turn:huge);run=adapter.prepare_run(run_id="huge",workspace=workspace,context=ApprovedBoundedContext("safe"))
 with pytest.raises(AgentAdapterError) as exc: await adapter.execute_turn(run,timeout_seconds=1)
 assert exc.value.code is AdapterFailure.OUTPUT_LIMIT
 bad=structured_api_adapter(sandboxes=sandboxes,reviews=reviews,source=lambda turn:raw("../escape"));run2=bad.prepare_run(run_id="bad",workspace=workspace,context=ApprovedBoundedContext("safe"))
 with pytest.raises(AgentAdapterError) as exc: await bad.execute_turn(run2,timeout_seconds=1)
 assert exc.value.code is AdapterFailure.UNSAFE_OUTPUT

@pytest.mark.asyncio
async def test_undeclared_sandbox_change_rejected(tmp_path):
 workspace,reviews,sandboxes=env(tmp_path)
 descriptor=AgentAdapterDescriptor("test_cli","Test CLI","local_cli",AdapterReadiness.READY,local_cli=True)
 def source(turn): (turn.sandbox/"undeclared.txt").write_text("bad");return raw()
 adapter=ContainedProposalAdapter(descriptor,sandboxes=sandboxes,reviews=reviews,source=source);run=adapter.prepare_run(run_id="cli",workspace=workspace,context=ApprovedBoundedContext("safe"));await adapter.execute_turn(run,timeout_seconds=1)
 with pytest.raises(AgentAdapterError) as exc:adapter.collect_result(run)
 assert exc.value.code is AdapterFailure.UNDECLARED_CHANGE and not (workspace/"undeclared.txt").exists()

@pytest.mark.asyncio
async def test_active_workspace_mutation_detected(tmp_path):
 workspace,reviews,sandboxes=env(tmp_path)
 def source(turn):(workspace/"main.txt").write_text("tampered");return raw()
 adapter=structured_api_adapter(sandboxes=sandboxes,reviews=reviews,source=source);run=adapter.prepare_run(run_id="integrity",workspace=workspace,context=ApprovedBoundedContext("safe"));await adapter.execute_turn(run,timeout_seconds=1)
 with pytest.raises(AgentAdapterError) as exc:adapter.collect_result(run)
 assert exc.value.code is AdapterFailure.ACTIVE_WORKSPACE_CHANGED

@pytest.mark.asyncio
async def test_command_suggestions_are_never_executed(tmp_path):
 workspace,reviews,sandboxes=env(tmp_path);marker=tmp_path/"pwned"
 adapter=structured_api_adapter(sandboxes=sandboxes,reviews=reviews,source=lambda turn:raw(commands=[{"command":f"touch {marker}","reason":"test"}]));run=adapter.prepare_run(run_id="commands",workspace=workspace,context=ApprovedBoundedContext("safe"));await adapter.execute_turn(run,timeout_seconds=1);adapter.collect_result(run)
 assert not marker.exists() and adapter.safe_diagnostics()["shell_execution"] is False

def test_codex_and_agy_remain_disabled_until_evidence_passes():
 descriptors=disabled_cli_descriptors();assert {x.adapter_id for x in descriptors}=={"codex_cli","agy_cli"};assert all(x.readiness is AdapterReadiness.DISABLED and x.local_cli and x.managed_sandbox_required for x in descriptors)

@pytest.mark.asyncio
async def test_verified_template_is_adapted_as_untrusted_sandbox_proposal(tmp_path):
 workspace,reviews,sandboxes=env(tmp_path);adapter=verified_template_adapter(sandboxes=sandboxes,reviews=reviews);run=adapter.prepare_run(run_id="template",workspace=workspace,context=ApprovedBoundedContext("create a simple esp32 blink project"));await adapter.execute_turn(run,timeout_seconds=1);result=adapter.collect_result(run)
 assert set(result.changed_files)=={"platformio.ini","src/main.cpp"} and not (workspace/"platformio.ini").exists()