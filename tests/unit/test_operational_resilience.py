import asyncio,json,sqlite3,sys
from pathlib import Path
import pytest
from backend.operations.resilience import (BudgetExceeded,BudgetLimits,BudgetTracker,CircuitOpen,DatabaseMaintenance,DiagnosticExporter,ProviderCircuitBreakers,ProviderResilience,ProviderTimedOut,RateLimitExceeded,SafeOperationalMetrics,SlidingWindowRateLimiter)
from backend.runtime.subprocess_mgr import ProcessConfig,SubprocessManager
from backend.state.domain_persistence import DomainDatabase,DomainRepository
from backend.state.durable_workflow import DurableWorkflowStateMachine,WorkflowState
from backend.state.workflow_events import DurableWorkflowEventHub,RESYNC

def test_budgets_and_rate_limits_fail_closed():
 budget=BudgetTracker(BudgetLimits(max_seconds=10,max_steps=1,max_input_tokens=2,max_output_tokens=2,max_cost_micros=2),started=0)
 budget.consume(steps=1,input_tokens=2,now=1)
 with pytest.raises(BudgetExceeded):budget.consume(steps=1,now=2)
 limiter=SlidingWindowRateLimiter(limit=1,window_seconds=10)
 limiter.acquire("provider",now=0)
 with pytest.raises(RateLimitExceeded):limiter.acquire("provider",now=1)
 limiter.acquire("provider",now=11)

@pytest.mark.asyncio
async def test_provider_timeout_opens_circuit_and_cancellation_propagates():
 circuits=ProviderCircuitBreakers(failure_threshold=2,recovery_seconds=30);metrics=SafeOperationalMetrics();guard=ProviderResilience(timeout_seconds=.01,circuits=circuits,metrics=metrics)
 async def slow():await asyncio.sleep(10)
 for _ in range(2):
  with pytest.raises(ProviderTimedOut):await guard.execute("remote",slow)
 with pytest.raises(CircuitOpen):await guard.execute("remote",slow)
 assert metrics.snapshot()["provider_timeouts"]==2
 guard2=ProviderResilience(timeout_seconds=10,metrics=metrics)
 task=asyncio.create_task(guard2.execute("cancelled",slow));await asyncio.sleep(.01);task.cancel()
 with pytest.raises(asyncio.CancelledError):await task
 assert metrics.snapshot()["cancellations"]==1

@pytest.mark.asyncio
async def test_telemetry_failure_never_replaces_provider_result():
 class BrokenMetrics:
  def safe_increment(self,*args,**kwargs):raise RuntimeError("telemetry unavailable")
 guard=ProviderResilience(metrics=BrokenMetrics())
 async def result():return {"workflow":"complete"}
 assert await guard.execute("provider",result)=={"workflow":"complete"}

def test_verified_backup_and_corruption_recovery(tmp_path):
 database=DomainDatabase(tmp_path/"state.db");repo=DomainRepository(database);repo.create_run({"run_id":"run","status":"draft","idempotency_key":"request"})
 maintenance=DatabaseMaintenance(database,tmp_path/"backups");backup=maintenance.backup();assert backup.exists()
 database.path.write_bytes(b"not a sqlite database")
 assert maintenance.recover_if_corrupt() is True
 assert DomainRepository(database).get_run("run")["run_id"]=="run"
 assert list(tmp_path.glob("state.db.corrupt-*"))

def test_database_contention_fails_without_partial_write(tmp_path):
 database=DomainDatabase(tmp_path/"state.db",timeout=.02);database.initialize();holder=database.connect();holder.execute("BEGIN EXCLUSIVE")
 try:
  with pytest.raises(sqlite3.OperationalError):DomainRepository(database).create_run({"run_id":"contended","status":"draft","idempotency_key":"key"})
 finally:holder.rollback();holder.close()
 assert DomainRepository(database).get_run("contended") is None

@pytest.mark.asyncio
async def test_websocket_loss_requests_replay_and_records_safe_metric(tmp_path):
 database=DomainDatabase(tmp_path/"state.db");machine=DurableWorkflowStateMachine(database);metrics=SafeOperationalMetrics();hub=DurableWorkflowEventHub(database,queue_size=1,metrics=metrics)
 machine.create(run_id="run",idempotency_key="create",actor="user",policy_decision="accepted",input_artifact_hashes=())
 queue,_=await hub.subscribe("run")
 machine.transition(run_id="run",expected_state=WorkflowState.DRAFT,expected_version=0,idempotency_key="one",actor="forgex.workflow",policy_decision="prepare",input_artifact_hashes=(),output_artifact_hashes=(),target_state=WorkflowState.PREPARING_CONTEXT)
 machine.transition(run_id="run",expected_state=WorkflowState.PREPARING_CONTEXT,expected_version=1,idempotency_key="two",actor="forgex.workflow",policy_decision="route",input_artifact_hashes=(),output_artifact_hashes=(),target_state=WorkflowState.ROUTING)
 await hub.publish_pending();assert await queue.get()==RESYNC;assert metrics.snapshot()["websocket_resyncs"]==1
 await hub.close()

@pytest.mark.asyncio
async def test_process_timeout_terminates_managed_process(tmp_path):
 manager=SubprocessManager();result=await manager.run(ProcessConfig(args=[sys.executable,"-c","import time; time.sleep(30)"],cwd=str(tmp_path),timeout_s=.05))
 assert result.timed_out and not result.success
 await manager.kill_all()
 assert not manager._active

def test_diagnostic_export_is_aggregate_and_secret_free(tmp_path):
 database=DomainDatabase(tmp_path/"state.db");database.initialize();metrics=SafeOperationalMetrics()
 class Logs:
  def export_logs(self):return json.dumps([{"log_type":"workflow","level":"ERROR","message":"API_KEY=super-secret","metadata":{"source":"raw code"}}])
 class Connections:
  def safe_diagnostics(self):return {"auth_files_read":False,"api_key":"should redact","connections":[]}
 class Reconciler:last_report={"status":"ready"}
 export=DiagnosticExporter(database=database,metrics=metrics,circuits=ProviderCircuitBreakers(),runtime_logger=Logs(),connection_registry=Connections(),reconciler=Reconciler()).export();raw=json.dumps(export).casefold()
 assert "super-secret" not in raw and "raw code" not in raw and "should redact" not in raw
 assert export["logs"]["raw_logs_included"] is False and export["connections"]["api_key"]=="[REDACTED]"


def test_retention_prunes_derived_records_but_preserves_append_only_events(tmp_path):
 database=DomainDatabase(tmp_path/"state.db");machine=DurableWorkflowStateMachine(database)
 machine.create(run_id="retained",idempotency_key="create-retained",actor="user",policy_decision="accepted",input_artifact_hashes=())
 machine.transition(run_id="retained",expected_state=WorkflowState.DRAFT,expected_version=0,idempotency_key="transition-retained",actor="forgex.workflow",policy_decision="prepare",input_artifact_hashes=(),output_artifact_hashes=(),target_state=WorkflowState.PREPARING_CONTEXT)
 with database.transaction() as db:
  db.execute("UPDATE workflow_event_outbox SET published_at='2000-01-01T00:00:00Z'")
  db.execute("INSERT INTO control_plane_commands VALUES('test','old-command','hash','{}','2000-01-01T00:00:00Z')")
 removed=DatabaseMaintenance(database,tmp_path/"backups").retain()
 assert removed["commands"]==1 and removed["outbox"]==1
 with database.connect() as db:assert db.execute("SELECT COUNT(*) FROM workflow_events WHERE run_id='retained'").fetchone()[0]==1

def test_startup_reconciliation_expires_stale_approval_and_creates_backup(tmp_path):
 from backend.operations.resilience import StartupReconciler
 database=DomainDatabase(tmp_path/"state.db");machine=DurableWorkflowStateMachine(database);metrics=SafeOperationalMetrics()
 machine.create(run_id="startup",idempotency_key="startup-create",actor="user",policy_decision="accepted",input_artifact_hashes=())
 DomainRepository(database).insert("approvals",{"approval_id":"expired","run_id":"startup","step_id":None,"status":"pending","action_code":"apply","idempotency_key":"expired-key","requested_at":"2000-01-01T00:00:00Z","resolved_at":None,"expires_at":"2000-01-01T00:00:00Z"})
 maintenance=DatabaseMaintenance(database,tmp_path/"backups");report=StartupReconciler(database,maintenance,machine,metrics).run(stale_after_seconds=10**9)
 assert report["expired_approvals"]==1 and (tmp_path/"backups"/report["backup"]).exists()
 with database.connect() as db:assert db.execute("SELECT status FROM approvals WHERE approval_id='expired'").fetchone()[0]=="expired"

