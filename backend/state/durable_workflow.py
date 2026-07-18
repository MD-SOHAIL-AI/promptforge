"""Durable workflow state machine backed by the domain SQLite database."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Iterable

from .domain_persistence import (DomainDatabase, DomainPersistenceError, EventSequenceError,
 IdempotencyConflict, StaleRunVersion, _decode, _json, _now, _safe_payload)

class WorkflowState(str,Enum):
 DRAFT="draft"; PREPARING_CONTEXT="preparing_context"; AWAITING_CONTEXT_CONSENT="awaiting_context_consent"
 ROUTING="routing"; GENERATING="generating"; VALIDATING="validating"; AWAITING_REVIEW="awaiting_review"
 AWAITING_APPLY_APPROVAL="awaiting_apply_approval"; APPLYING="applying"; AWAITING_BUILD="awaiting_build"
 BUILDING="building"; REPAIRING="repairing"; AWAITING_FLASH_APPROVAL="awaiting_flash_approval"
 FLASHING="flashing"; AWAITING_MONITOR="awaiting_monitor"; MONITORING="monitoring"; VERIFYING="verifying"
 COMPLETED="completed"; FAILED="failed"; CANCELLED="cancelled"; TIMED_OUT="timed_out"

TERMINAL=frozenset({WorkflowState.COMPLETED,WorkflowState.FAILED,WorkflowState.CANCELLED,WorkflowState.TIMED_OUT})
ALLOWED={
 WorkflowState.DRAFT:{WorkflowState.PREPARING_CONTEXT},
 WorkflowState.PREPARING_CONTEXT:{WorkflowState.AWAITING_CONTEXT_CONSENT,WorkflowState.ROUTING},
 WorkflowState.AWAITING_CONTEXT_CONSENT:{WorkflowState.ROUTING},
 WorkflowState.ROUTING:{WorkflowState.GENERATING},
 WorkflowState.GENERATING:{WorkflowState.VALIDATING},
 WorkflowState.VALIDATING:{WorkflowState.AWAITING_REVIEW,WorkflowState.REPAIRING},
 WorkflowState.AWAITING_REVIEW:{WorkflowState.AWAITING_APPLY_APPROVAL,WorkflowState.REPAIRING},
 WorkflowState.AWAITING_APPLY_APPROVAL:{WorkflowState.APPLYING},
 WorkflowState.APPLYING:{WorkflowState.AWAITING_BUILD},
 WorkflowState.AWAITING_BUILD:{WorkflowState.BUILDING,WorkflowState.VERIFYING},
 WorkflowState.BUILDING:{WorkflowState.AWAITING_FLASH_APPROVAL,WorkflowState.VERIFYING,WorkflowState.REPAIRING},
 WorkflowState.REPAIRING:{WorkflowState.GENERATING,WorkflowState.VALIDATING,WorkflowState.AWAITING_APPLY_APPROVAL},
 WorkflowState.AWAITING_FLASH_APPROVAL:{WorkflowState.FLASHING,WorkflowState.VERIFYING},
 WorkflowState.FLASHING:{WorkflowState.AWAITING_MONITOR,WorkflowState.VERIFYING},
 WorkflowState.AWAITING_MONITOR:{WorkflowState.MONITORING,WorkflowState.VERIFYING},
 WorkflowState.MONITORING:{WorkflowState.VERIFYING},
 WorkflowState.VERIFYING:{WorkflowState.COMPLETED,WorkflowState.REPAIRING},
}
EXECUTOR_ACTORS={WorkflowState.APPLYING:"forgex.apply_executor",WorkflowState.BUILDING:"forgex.build_executor",WorkflowState.FLASHING:"forgex.flash_executor",WorkflowState.MONITORING:"forgex.monitor_executor"}

class InvalidTransition(DomainPersistenceError): pass
class ExecutorAuthorityError(DomainPersistenceError): pass
class LeaseConflict(DomainPersistenceError): pass
class ArtifactPersistenceFailed(DomainPersistenceError):
 code="ARTIFACT_PERSISTENCE_FAILED"
 safe_message="Authoritative workflow artifact could not be persisted."
 def __init__(self):super().__init__(self.safe_message)

@dataclass(frozen=True,slots=True)
class TransitionResult:
 run_id:str; state:WorkflowState; version:int; event_sequence:int; duplicate:bool=False
@dataclass(frozen=True,slots=True)
class OperationLease:
 lease_id:str; run_id:str; operation:str; owner_id:str; fencing_token:int; acquired_at:str; expires_at:str
@dataclass(frozen=True,slots=True)
class AuthoritativeArtifact:
 artifact_id:str; run_id:str; artifact_type:str; content_hash:str; storage_reference:str; size_bytes:int=0; step_id:str|None=None

class DurableWorkflowStateMachine:
 def __init__(self,database:DomainDatabase): self.database=database; database.initialize()
 def create(self,*,run_id:str,idempotency_key:str,actor:str,policy_decision:str,input_artifact_hashes:Iterable[str],output_artifact_hashes:Iterable[str]=()) -> TransitionResult:
  inputs=_hashes(input_artifact_hashes); outputs=_hashes(output_artifact_hashes); now=_now()
  payload=_safe_payload({"run_id":run_id,"status":"draft","idempotency_key":idempotency_key,"created_by":actor,"policy_decision":policy_decision,"input_artifact_hashes":inputs,"output_artifact_hashes":outputs})
  with self.database.transaction() as db:
   existing=db.execute("SELECT run_id,status,version FROM workflow_runs WHERE idempotency_key=?",(idempotency_key,)).fetchone()
   if existing:
    if existing[0]!=run_id: raise IdempotencyConflict("idempotency key belongs to another run")
    return TransitionResult(run_id,WorkflowState(existing[1]),existing[2],0,True)
   db.execute("INSERT INTO workflow_runs(run_id,status,version,idempotency_key,payload_json,created_at,updated_at) VALUES(?,?,0,?,?,?,?)",(run_id,"draft",idempotency_key,_json(payload),now,now))
  return TransitionResult(run_id,WorkflowState.DRAFT,0,0)
 def transition(self,*,run_id:str,expected_state:WorkflowState,expected_version:int,idempotency_key:str,actor:str,policy_decision:str,input_artifact_hashes:Iterable[str],output_artifact_hashes:Iterable[str],target_state:WorkflowState,safe_message:str|None=None,artifact:AuthoritativeArtifact|None=None) -> TransitionResult:
  inputs,outputs=_hashes(input_artifact_hashes),_hashes(output_artifact_hashes)
  if not actor.strip() or not policy_decision.strip() or not idempotency_key.strip(): raise InvalidTransition("actor, policy decision, and idempotency key are required")
  _validate_edge(expected_state,target_state)
  required=EXECUTOR_ACTORS.get(target_state)
  if required and actor!=required: raise ExecutorAuthorityError(f"{target_state.value} is owned by {required}")
  now=_now()
  with self.database.transaction() as db:
   prior=db.execute("SELECT expected_state,expected_version,target_state,actor,result_version,event_sequence FROM workflow_commands WHERE run_id=? AND idempotency_key=?",(run_id,idempotency_key)).fetchone()
   if prior:
    if (prior[0],prior[1],prior[2],prior[3])!=(expected_state.value,expected_version,target_state.value,actor): raise IdempotencyConflict("idempotency key was reused for a different command")
    if artifact is not None:self._persist_authoritative_artifact(db,artifact,now,require_existing=True)
    return TransitionResult(run_id,target_state,prior[4],prior[5],True)
   row=db.execute("SELECT status,version,payload_json FROM workflow_runs WHERE run_id=?",(run_id,)).fetchone()
   if not row: raise InvalidTransition("run not found")
   if required:
    operation=target_state.value.removesuffix("ing")
    lease=db.execute("SELECT 1 FROM operation_leases WHERE run_id=? AND operation=? AND owner_id=? AND expires_at>?",(run_id,operation,actor,now)).fetchone()
    if not lease: raise LeaseConflict("executor transition requires a live owned operation lease")
   if row[0]!=expected_state.value or row[1]!=expected_version: raise StaleRunVersion("run state or version is stale")
   sequence=db.execute("SELECT COALESCE(MAX(sequence),0)+1 FROM workflow_events WHERE run_id=?",(run_id,)).fetchone()[0]
   payload=_decode(row[2]); payload.update({"status":target_state.value,"last_actor":actor,"policy_decision":policy_decision,"input_artifact_hashes":inputs,"output_artifact_hashes":outputs})
   new_version=expected_version+1
   cur=db.execute("UPDATE workflow_runs SET status=?,version=?,safe_summary=?,payload_json=?,updated_at=? WHERE run_id=? AND status=? AND version=?",(target_state.value,new_version,safe_message,_json(payload),now,run_id,expected_state.value,expected_version))
   if cur.rowcount!=1: raise StaleRunVersion("run update lost an optimistic race")
   event_type="terminal" if target_state in TERMINAL else "transition"
   event={"event_id":f"{run_id}.{sequence}","run_id":run_id,"sequence":sequence,"event_type":event_type,"from_state":expected_state.value,"to_state":target_state.value,"actor":actor,"policy_decision":policy_decision,"input_artifact_hashes":inputs,"output_artifact_hashes":outputs,"safe_message":safe_message}
   event_payload=_json(_safe_payload(event))
   db.execute("INSERT INTO workflow_events VALUES(?,?,?,?,?,?,?)",(run_id,sequence,event["event_id"],event_type,safe_message,event_payload,now))
   db.execute("INSERT INTO workflow_event_outbox(run_id,sequence,payload_json,committed_at) VALUES(?,?,?,?)",(run_id,sequence,event_payload,now))
   role="user" if actor=="user" or actor.startswith("user.") else "system" if actor.startswith("forgex.") else "assistant"
   content=safe_message or f"Workflow moved to {target_state.value.replace('_',' ')}."
   db.execute("INSERT INTO conversation_messages VALUES(?,?,?,?,?,?)",(f"{run_id}.message.{sequence}",run_id,sequence,role,content,now))
   db.execute("INSERT INTO workflow_commands VALUES(?,?,?,?,?,?,?,?,?)",(run_id,idempotency_key,expected_state.value,expected_version,target_state.value,actor,new_version,sequence,now))
   if artifact is not None:self._persist_authoritative_artifact(db,artifact,now)
   if target_state in TERMINAL or target_state is WorkflowState.REPAIRING:
    db.execute("UPDATE approvals SET status='cancelled',resolved_at=? WHERE run_id=? AND status='pending'",(now,run_id))
  return TransitionResult(run_id,target_state,new_version,sequence)
 def transition_with_artifact(self,*,artifact:AuthoritativeArtifact,**command) -> TransitionResult:
  if artifact.run_id!=command.get("run_id"):
   try:raise DomainPersistenceError("artifact run does not match transition run")
   except DomainPersistenceError as exc:raise ArtifactPersistenceFailed() from exc
  try:return self.transition(artifact=artifact,**command)
  except (ArtifactPersistenceFailed,InvalidTransition,ExecutorAuthorityError,LeaseConflict,StaleRunVersion,IdempotencyConflict):raise
  except Exception as exc:raise ArtifactPersistenceFailed() from exc
 def _persist_authoritative_artifact(self,db:sqlite3.Connection,artifact:AuthoritativeArtifact,now:str,*,require_existing:bool=False)->None:
  try:
   if artifact.run_id.strip()=="" or artifact.artifact_id.strip()=="" or artifact.artifact_type.strip()=="" or artifact.storage_reference.strip()=="" or artifact.size_bytes<0:raise DomainPersistenceError("artifact metadata is invalid")
   _hashes((artifact.content_hash,))
   payload=_safe_payload({"artifact_id":artifact.artifact_id,"run_id":artifact.run_id,"step_id":artifact.step_id,"artifact_type":artifact.artifact_type,"content_hash":artifact.content_hash,"size_bytes":artifact.size_bytes,"storage_reference":artifact.storage_reference})
   existing=db.execute("SELECT run_id,step_id,artifact_type,content_hash,size_bytes,storage_reference FROM artifacts WHERE artifact_id=?",(artifact.artifact_id,)).fetchone()
   expected=(artifact.run_id,artifact.step_id,artifact.artifact_type,artifact.content_hash,artifact.size_bytes,artifact.storage_reference)
   if existing:
    if tuple(existing)!=expected:raise IdempotencyConflict("artifact identity conflicts with a different record")
    return
   if require_existing:raise DomainPersistenceError("committed command artifact is missing")
   db.execute("INSERT INTO artifacts(artifact_id,run_id,step_id,artifact_type,content_hash,size_bytes,storage_reference,payload_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)",(artifact.artifact_id,*expected,_json(payload),now))
  except ArtifactPersistenceFailed:raise
  except Exception as exc:raise ArtifactPersistenceFailed() from exc
 def cancel(self,**command) -> TransitionResult: return self.transition(target_state=WorkflowState.CANCELLED,**command)
 def timeout(self,**command) -> TransitionResult: return self.transition(target_state=WorkflowState.TIMED_OUT,**command)
 def get(self,run_id:str) -> TransitionResult:
  with self.database.connect() as db:
   row=db.execute("SELECT status,version,(SELECT COALESCE(MAX(sequence),0) FROM workflow_events WHERE run_id=?) FROM workflow_runs WHERE run_id=?",(run_id,run_id)).fetchone()
  if not row: raise InvalidTransition("run not found")
  return TransitionResult(run_id,WorkflowState(row[0]),row[1],row[2])
 def acquire_lease(self,*,run_id:str,operation:str,owner_id:str,ttl_seconds:int,at:datetime|None=None) -> OperationLease:
  if operation not in {"apply","build","flash","monitor"} or ttl_seconds<1: raise LeaseConflict("invalid lease request")
  now=at or datetime.now(timezone.utc); expires=now+timedelta(seconds=ttl_seconds); stamp=_format(now)
  with self.database.transaction() as db:
   row=db.execute("SELECT lease_id,owner_id,fencing_token,expires_at FROM operation_leases WHERE run_id=? AND operation=?",(run_id,operation)).fetchone()
   if row and _parse(row[3])>now: raise LeaseConflict("operation already has an active lease")
   token=(row[2]+1) if row else 1; lease_id=f"{run_id}.{operation}.{token}"
   db.execute("INSERT INTO operation_leases VALUES(?,?,?,?,?,?,?) ON CONFLICT(run_id,operation) DO UPDATE SET lease_id=excluded.lease_id,owner_id=excluded.owner_id,fencing_token=excluded.fencing_token,acquired_at=excluded.acquired_at,expires_at=excluded.expires_at",(lease_id,run_id,operation,owner_id,token,stamp,_format(expires)))
  return OperationLease(lease_id,run_id,operation,owner_id,token,stamp,_format(expires))
 def renew_lease(self,lease:OperationLease,*,ttl_seconds:int,at:datetime|None=None) -> OperationLease:
  now=at or datetime.now(timezone.utc); expires=now+timedelta(seconds=ttl_seconds)
  with self.database.transaction() as db:
   cur=db.execute("UPDATE operation_leases SET expires_at=? WHERE lease_id=? AND owner_id=? AND fencing_token=? AND expires_at>?",(_format(expires),lease.lease_id,lease.owner_id,lease.fencing_token,_format(now)))
   if cur.rowcount!=1: raise LeaseConflict("lease is stale, expired, or replaced")
  return OperationLease(lease.lease_id,lease.run_id,lease.operation,lease.owner_id,lease.fencing_token,lease.acquired_at,_format(expires))
 def release_lease(self,lease:OperationLease) -> None:
  with self.database.transaction() as db:
   cur=db.execute("DELETE FROM operation_leases WHERE lease_id=? AND owner_id=? AND fencing_token=?",(lease.lease_id,lease.owner_id,lease.fencing_token))
   if cur.rowcount!=1: raise LeaseConflict("lease is stale or replaced")
 def recover_stale(self,*,older_than:datetime) -> tuple[TransitionResult,...]:
  recovered=[]; now=datetime.now(timezone.utc)
  with self.database.connect() as db: rows=db.execute("SELECT run_id,status,version,updated_at FROM workflow_runs WHERE status NOT IN ('completed','failed','cancelled','timed_out') AND updated_at<?",(_format(older_than),)).fetchall()
  for row in rows:
   with self.database.connect() as db:
    lease=db.execute("SELECT expires_at FROM operation_leases WHERE run_id=? ORDER BY expires_at DESC LIMIT 1",(row[0],)).fetchone()
   if lease and _parse(lease[0])>now: continue
   recovered.append(self.transition(run_id=row[0],expected_state=WorkflowState(row[1]),expected_version=row[2],idempotency_key=f"stale-recovery-{row[2]}",actor="forgex.recovery",policy_decision="stale_run_fail_closed",input_artifact_hashes=(),output_artifact_hashes=(),target_state=WorkflowState.FAILED,safe_message="Run safely failed after its operation lease became stale."))
  return tuple(recovered)

def _validate_edge(source:WorkflowState,target:WorkflowState)->None:
 if source in TERMINAL: raise InvalidTransition("terminal state cannot transition")
 if target in {WorkflowState.CANCELLED,WorkflowState.TIMED_OUT,WorkflowState.FAILED}: return
 if target not in ALLOWED.get(source,set()): raise InvalidTransition(f"invalid transition: {source.value} -> {target.value}")
def _hashes(values:Iterable[str])->list[str]:
 result=list(values)
 if any(not isinstance(x,str) or len(x)!=64 or any(c not in "0123456789abcdef" for c in x) for x in result): raise InvalidTransition("artifact hashes must be lowercase SHA-256")
 return result
def _format(value:datetime)->str:
 if value.tzinfo is None: raise LeaseConflict("lease time must be timezone-aware")
 return value.astimezone(timezone.utc).isoformat().replace("+00:00","Z")
def _parse(value:str)->datetime:return datetime.fromisoformat(value.replace("Z","+00:00"))
