import asyncio
import pytest
from backend.state.domain_persistence import DomainDatabase,DomainPersistenceError
from backend.state.durable_workflow import DurableWorkflowStateMachine,WorkflowState
from backend.state.workflow_events import DurableWorkflowEventHub,RESYNC,WorkflowProjectionRepository
H="b"*64

def setup(tmp_path):
 db=DomainDatabase(tmp_path/"events.db"); machine=DurableWorkflowStateMachine(db); machine.create(run_id="run",idempotency_key="create",actor="user",policy_decision="policy",input_artifact_hashes=(H,)); return db,machine
def transition(machine,key,state,version,target,message=None):
 return machine.transition(run_id="run",expected_state=state,expected_version=version,idempotency_key=key,actor="controller",policy_decision="policy",input_artifact_hashes=(H,),output_artifact_hashes=(H,),target_state=target,safe_message=message)

def test_event_state_outbox_and_message_commit_atomically(tmp_path):
 db,m=setup(tmp_path); transition(m,"one",WorkflowState.DRAFT,0,WorkflowState.PREPARING_CONTEXT,"Preparing bounded context.")
 with db.connect() as con:
  assert tuple(con.execute("SELECT status,version FROM workflow_runs").fetchone())==("preparing_context",1)
  assert con.execute("SELECT count(*) FROM workflow_events").fetchone()[0]==1
  assert con.execute("SELECT count(*) FROM workflow_event_outbox").fetchone()[0]==1
  assert tuple(con.execute("SELECT role,content FROM conversation_messages").fetchone())==("assistant","Preparing bounded context.")

def test_unsafe_event_rolls_back_without_state_or_outbox(tmp_path):
 db,m=setup(tmp_path)
 with pytest.raises(DomainPersistenceError): transition(m,"bad",WorkflowState.DRAFT,0,WorkflowState.PREPARING_CONTEXT,"token=raw-secret")
 with db.connect() as con:
  assert tuple(con.execute("SELECT status,version FROM workflow_runs").fetchone())==("draft",0)
  assert con.execute("SELECT count(*) FROM workflow_events").fetchone()[0]==0
  assert con.execute("SELECT count(*) FROM workflow_event_outbox").fetchone()[0]==0

def test_projection_replay_after_sequence_and_backend_restart(tmp_path):
 db,m=setup(tmp_path); transition(m,"one",WorkflowState.DRAFT,0,WorkflowState.PREPARING_CONTEXT); transition(m,"two",WorkflowState.PREPARING_CONTEXT,1,WorkflowState.ROUTING)
 first=WorkflowProjectionRepository(db).projection("run",after_sequence=1); assert [e["sequence"] for e in first["events"]]==[2]
 restarted=WorkflowProjectionRepository(DomainDatabase(tmp_path/"events.db")); projection=restarted.projection("run",after_sequence=0)
 assert projection["last_sequence"]==2 and [m["event_sequence"] for m in projection["messages"]]==[1,2]

@pytest.mark.asyncio
async def test_live_delivery_replay_and_restart_are_lossless(tmp_path):
 db,m=setup(tmp_path); transition(m,"one",WorkflowState.DRAFT,0,WorkflowState.PREPARING_CONTEXT)
 hub=DurableWorkflowEventHub(db,poll_interval=.01); queue,replay=await hub.subscribe("run",after_sequence=0); assert [e["sequence"] for e in replay]==[1]
 await hub.publish_pending(); live=await asyncio.wait_for(queue.get(),1); assert live["sequence"]==1
 await hub.close()
 transition(m,"two",WorkflowState.PREPARING_CONTEXT,1,WorkflowState.ROUTING)
 restarted=DurableWorkflowEventHub(DomainDatabase(tmp_path/"events.db"),poll_interval=.01); _,replayed=await restarted.subscribe("run",after_sequence=1)
 assert [e["sequence"] for e in replayed]==[2]; await restarted.close()

@pytest.mark.asyncio
async def test_slow_client_is_bounded_and_forced_to_replay(tmp_path):
 db,m=setup(tmp_path); hub=DurableWorkflowEventHub(db,queue_size=1,poll_interval=10); queue,_=await hub.subscribe("run")
 transition(m,"one",WorkflowState.DRAFT,0,WorkflowState.PREPARING_CONTEXT); transition(m,"two",WorkflowState.PREPARING_CONTEXT,1,WorkflowState.ROUTING)
 await hub.publish_pending(limit=10); assert await queue.get()==RESYNC; await hub.close()

def test_duplicate_and_reordered_events_are_database_rejected(tmp_path):
 db,m=setup(tmp_path); transition(m,"one",WorkflowState.DRAFT,0,WorkflowState.PREPARING_CONTEXT)
 with db.transaction() as con:
  with pytest.raises(Exception): con.execute("INSERT INTO workflow_event_outbox(run_id,sequence,payload_json,committed_at) VALUES('run',1,'{}','now')")
 projection=WorkflowProjectionRepository(db).projection("run"); assert [e["sequence"] for e in projection["events"]]==[1]
