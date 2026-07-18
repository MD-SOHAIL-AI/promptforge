import threading
from datetime import datetime,timedelta,timezone
import pytest
from backend.state.domain_persistence import DomainDatabase,DomainRepository,StaleRunVersion
from backend.state.durable_workflow import (ALLOWED,TERMINAL,DurableWorkflowStateMachine,ExecutorAuthorityError,InvalidTransition,LeaseConflict,WorkflowState,_validate_edge)
H="a"*64

def machine(tmp_path): return DurableWorkflowStateMachine(DomainDatabase(tmp_path/"workflow.db"))
def create(m,run="run-1"):
 return m.create(run_id=run,idempotency_key=f"create-{run}",actor="user",policy_decision="policy-1",input_artifact_hashes=(H,))
def command(run,state,version,key,target,actor="controller"):
 return dict(run_id=run,expected_state=state,expected_version=version,idempotency_key=key,actor=actor,policy_decision="policy-1",input_artifact_hashes=(H,),output_artifact_hashes=(H,),target_state=target)

def test_complete_transition_matrix_fails_closed():
 states=set(WorkflowState)
 for source in states:
  for target in states:
   allowed=source not in TERMINAL and (target in ALLOWED.get(source,set()) or target in {WorkflowState.FAILED,WorkflowState.CANCELLED,WorkflowState.TIMED_OUT})
   if allowed: _validate_edge(source,target)
   else:
    with pytest.raises(InvalidTransition): _validate_edge(source,target)

def test_duplicate_command_returns_original_result_and_one_event(tmp_path):
 m=machine(tmp_path); create(m); cmd=command("run-1",WorkflowState.DRAFT,0,"prepare",WorkflowState.PREPARING_CONTEXT)
 first=m.transition(**cmd); duplicate=m.transition(**cmd)
 assert first.version==duplicate.version==1 and duplicate.duplicate
 with m.database.connect() as db: assert db.execute("SELECT count(*) FROM workflow_events").fetchone()[0]==1

def test_concurrent_actions_use_optimistic_version(tmp_path):
 m=machine(tmp_path); create(m); gate=threading.Barrier(2); outcomes=[]
 def act(key):
  gate.wait()
  try:m.transition(**command("run-1",WorkflowState.DRAFT,0,key,WorkflowState.PREPARING_CONTEXT));outcomes.append("ok")
  except StaleRunVersion:outcomes.append("stale")
 ts=[threading.Thread(target=act,args=(k,)) for k in ("a","b")];[t.start() for t in ts];[t.join() for t in ts]
 assert sorted(outcomes)==["ok","stale"]

def test_restart_resume_uses_durable_state_and_version(tmp_path):
 path=tmp_path/"workflow.db"; first=DurableWorkflowStateMachine(DomainDatabase(path));create(first)
 first.transition(**command("run-1",WorkflowState.DRAFT,0,"prepare",WorkflowState.PREPARING_CONTEXT))
 resumed=DurableWorkflowStateMachine(DomainDatabase(path)); state=resumed.get("run-1")
 assert state.state is WorkflowState.PREPARING_CONTEXT and state.version==1 and state.event_sequence==1
 resumed.transition(**command("run-1",state.state,state.version,"route",WorkflowState.ROUTING))

def test_cancellation_timeout_and_exactly_one_terminal_event(tmp_path):
 m=machine(tmp_path);create(m,"cancel-run"); result=m.cancel(**{k:v for k,v in command("cancel-run",WorkflowState.DRAFT,0,"cancel",WorkflowState.CANCELLED).items() if k!="target_state"})
 assert result.state is WorkflowState.CANCELLED
 assert m.cancel(**{k:v for k,v in command("cancel-run",WorkflowState.DRAFT,0,"cancel",WorkflowState.CANCELLED).items() if k!="target_state"}).duplicate
 with pytest.raises(InvalidTransition): m.transition(**command("cancel-run",WorkflowState.CANCELLED,1,"again",WorkflowState.FAILED))
 create(m,"timeout-run"); assert m.timeout(**{k:v for k,v in command("timeout-run",WorkflowState.DRAFT,0,"timeout",WorkflowState.TIMED_OUT).items() if k!="target_state"}).state is WorkflowState.TIMED_OUT
 with m.database.connect() as db:
  assert db.execute("SELECT count(*) FROM workflow_events WHERE event_type='terminal'").fetchone()[0]==2

def test_executor_states_reject_provider_authority(tmp_path):
 m=machine(tmp_path);create(m)
 with pytest.raises(ExecutorAuthorityError): m.transition(**command("run-1",WorkflowState.AWAITING_APPLY_APPROVAL,0,"apply",WorkflowState.APPLYING,actor="provider.openrouter"))
 # Failure occurs before state lookup, proving authority is independently enforced.

def test_renewable_and_stale_operation_leases(tmp_path):
 m=machine(tmp_path);create(m); now=datetime.now(timezone.utc)
 lease=m.acquire_lease(run_id="run-1",operation="build",owner_id="worker-1",ttl_seconds=10,at=now)
 renewed=m.renew_lease(lease,ttl_seconds=20,at=now+timedelta(seconds=1)); assert renewed.expires_at>lease.expires_at
 with pytest.raises(LeaseConflict): m.acquire_lease(run_id="run-1",operation="build",owner_id="worker-2",ttl_seconds=10,at=now+timedelta(seconds=2))
 replacement=m.acquire_lease(run_id="run-1",operation="build",owner_id="worker-2",ttl_seconds=10,at=now+timedelta(seconds=30)); assert replacement.fencing_token==2
 with pytest.raises(LeaseConflict): m.renew_lease(renewed,ttl_seconds=10,at=now+timedelta(seconds=31))

def test_safe_stale_recovery_respects_active_lease(tmp_path):
 m=machine(tmp_path);create(m); old=datetime.now(timezone.utc)-timedelta(hours=2)
 with m.database.transaction() as db: db.execute("UPDATE workflow_runs SET updated_at=? WHERE run_id='run-1'",(old.isoformat(),))
 lease=m.acquire_lease(run_id="run-1",operation="apply",owner_id="worker",ttl_seconds=3600)
 assert m.recover_stale(older_than=datetime.now(timezone.utc)-timedelta(hours=1))==()
 m.release_lease(lease); recovered=m.recover_stale(older_than=datetime.now(timezone.utc)-timedelta(hours=1))
 assert recovered[0].state is WorkflowState.FAILED

def test_approval_invalidation_on_cancel_and_repair(tmp_path):
 m=machine(tmp_path);create(m); repo=DomainRepository(m.database)
 repo.insert("approvals",{"approval_id":"approval","run_id":"run-1","step_id":None,"status":"pending","action_code":"apply","idempotency_key":"approval-key","requested_at":"2026-01-01T00:00:00Z","resolved_at":None})
 m.cancel(**{k:v for k,v in command("run-1",WorkflowState.DRAFT,0,"cancel",WorkflowState.CANCELLED).items() if k!="target_state"})
 with m.database.connect() as db: row=db.execute("SELECT status,resolved_at FROM approvals WHERE approval_id='approval'").fetchone()
 assert row[0]=="cancelled" and row[1] is not None

def test_executor_transition_requires_matching_live_lease(tmp_path):
 m=machine(tmp_path);create(m)
 with m.database.transaction() as db: db.execute("UPDATE workflow_runs SET status='awaiting_apply_approval',payload_json=? WHERE run_id='run-1'",('{"run_id":"run-1","status":"awaiting_apply_approval"}',))
 with pytest.raises(LeaseConflict): m.transition(**command("run-1",WorkflowState.AWAITING_APPLY_APPROVAL,0,"apply-no-lease",WorkflowState.APPLYING,actor="forgex.apply_executor"))
 m.acquire_lease(run_id="run-1",operation="apply",owner_id="forgex.apply_executor",ttl_seconds=60)
 assert m.transition(**command("run-1",WorkflowState.AWAITING_APPLY_APPROVAL,0,"apply-with-lease",WorkflowState.APPLYING,actor="forgex.apply_executor")).state is WorkflowState.APPLYING
