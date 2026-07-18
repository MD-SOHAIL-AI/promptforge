"""Migration-managed SQLite persistence for versioned domain state.

Only bounded metadata and safe summaries belong here. Credentials, raw auth
output, prompts, responses, artifact bytes, and logs are explicitly rejected.
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

SCHEMA_VERSION = 5
MAX_JSON_BYTES = 16 * 1024
MAX_TEXT = 2_000
FORBIDDEN_KEYS = ("api_key", "apikey", "authorization", "credential", "password", "secret", "token", "auth_output", "raw_prompt", "raw_response", "prompt", "response", "log")
FORBIDDEN_VALUES = ("-----begin private key-----", "password=", "api_key=", "authorization: bearer ", "token=", "secret=")

class DomainPersistenceError(RuntimeError): pass
class StaleRunVersion(DomainPersistenceError): pass
class EventSequenceError(DomainPersistenceError): pass
class IdempotencyConflict(DomainPersistenceError): pass
class CorruptDatabase(DomainPersistenceError): pass

MIGRATIONS: tuple[tuple[int, str], ...] = ((1, r'''
CREATE TABLE connections (
 connection_id TEXT PRIMARY KEY, provider_id TEXT NOT NULL, account_name TEXT NOT NULL,
 provider_type TEXT NOT NULL, auth_type TEXT NOT NULL, enabled INTEGER NOT NULL CHECK(enabled IN(0,1)),
 payload_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 UNIQUE(provider_id, account_name)
);
CREATE TABLE connection_status (
 connection_id TEXT PRIMARY KEY REFERENCES connections(connection_id) ON DELETE CASCADE,
 auth_state TEXT NOT NULL, transport_status TEXT NOT NULL, detected INTEGER NOT NULL CHECK(detected IN(0,1)),
 checked_at TEXT NOT NULL, safe_message TEXT, payload_json TEXT NOT NULL
);
CREATE TABLE model_endpoints (
 endpoint_id TEXT PRIMARY KEY, connection_id TEXT NOT NULL REFERENCES connections(connection_id) ON DELETE CASCADE,
 model_id TEXT NOT NULL, enabled INTEGER NOT NULL CHECK(enabled IN(0,1)), payload_json TEXT NOT NULL,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(connection_id, model_id)
);
CREATE TABLE routing_policies (
 policy_id TEXT PRIMARY KEY, policy_version INTEGER NOT NULL CHECK(policy_version>0), name TEXT NOT NULL,
 payload_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE routing_decisions (
 decision_id TEXT PRIMARY KEY, policy_id TEXT NOT NULL REFERENCES routing_policies(policy_id),
 connection_id TEXT REFERENCES connections(connection_id), status TEXT NOT NULL, reason_code TEXT,
 idempotency_key TEXT NOT NULL UNIQUE, payload_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE agent_profiles (
 profile_id TEXT PRIMARY KEY, name TEXT NOT NULL, active_version INTEGER NOT NULL CHECK(active_version>0),
 payload_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE agent_profile_versions (
 profile_id TEXT NOT NULL REFERENCES agent_profiles(profile_id) ON DELETE CASCADE, version INTEGER NOT NULL CHECK(version>0),
 connection_id TEXT NOT NULL REFERENCES connections(connection_id), endpoint_id TEXT REFERENCES model_endpoints(endpoint_id),
 adapter_id TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(profile_id, version)
);
CREATE TABLE workflow_runs (
 run_id TEXT PRIMARY KEY, profile_id TEXT REFERENCES agent_profiles(profile_id), profile_version INTEGER,
 status TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 0 CHECK(version>=0), idempotency_key TEXT NOT NULL UNIQUE,
 safe_summary TEXT, failure_code TEXT, payload_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE workflow_steps (
 run_id TEXT NOT NULL REFERENCES workflow_runs(run_id) ON DELETE CASCADE, step_id TEXT NOT NULL,
 ordinal INTEGER NOT NULL CHECK(ordinal>=0), status TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 0 CHECK(version>=0),
 payload_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(run_id, step_id), UNIQUE(run_id, ordinal)
);
CREATE TABLE workflow_events (
 run_id TEXT NOT NULL REFERENCES workflow_runs(run_id) ON DELETE CASCADE, sequence INTEGER NOT NULL CHECK(sequence>0),
 event_id TEXT NOT NULL UNIQUE, event_type TEXT NOT NULL, safe_message TEXT, payload_json TEXT NOT NULL,
 created_at TEXT NOT NULL, PRIMARY KEY(run_id, sequence)
);
CREATE TRIGGER workflow_events_no_update BEFORE UPDATE ON workflow_events BEGIN SELECT RAISE(ABORT,'workflow_events_append_only'); END;
CREATE TRIGGER workflow_events_no_delete BEFORE DELETE ON workflow_events BEGIN SELECT RAISE(ABORT,'workflow_events_append_only'); END;
CREATE TABLE approvals (
 approval_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES workflow_runs(run_id) ON DELETE CASCADE,
 step_id TEXT, status TEXT NOT NULL, action_code TEXT NOT NULL, idempotency_key TEXT NOT NULL UNIQUE,
 payload_json TEXT NOT NULL, requested_at TEXT NOT NULL, resolved_at TEXT
);
CREATE TABLE artifacts (
 artifact_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES workflow_runs(run_id) ON DELETE CASCADE,
 step_id TEXT, artifact_type TEXT NOT NULL, content_hash TEXT NOT NULL, size_bytes INTEGER NOT NULL CHECK(size_bytes>=0),
 storage_reference TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE operation_leases (
 lease_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES workflow_runs(run_id) ON DELETE CASCADE,
 operation TEXT NOT NULL, owner_id TEXT NOT NULL, fencing_token INTEGER NOT NULL CHECK(fencing_token>0),
 acquired_at TEXT NOT NULL, expires_at TEXT NOT NULL, UNIQUE(run_id, operation)
);
CREATE TABLE usage_records (
 usage_id TEXT PRIMARY KEY, connection_id TEXT REFERENCES connections(connection_id), run_id TEXT REFERENCES workflow_runs(run_id),
 idempotency_key TEXT NOT NULL UNIQUE, input_units INTEGER NOT NULL DEFAULT 0 CHECK(input_units>=0),
 output_units INTEGER NOT NULL DEFAULT 0 CHECK(output_units>=0), cost_micros INTEGER CHECK(cost_micros>=0),
 payload_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX idx_status_auth ON connection_status(auth_state, transport_status);
CREATE INDEX idx_endpoints_connection ON model_endpoints(connection_id);
CREATE INDEX idx_decisions_policy ON routing_decisions(policy_id, created_at);
CREATE INDEX idx_runs_status ON workflow_runs(status, updated_at);
CREATE INDEX idx_events_run ON workflow_events(run_id, sequence);
CREATE INDEX idx_approvals_run ON approvals(run_id, status);
CREATE INDEX idx_artifacts_run ON artifacts(run_id, created_at);
CREATE INDEX idx_usage_connection ON usage_records(connection_id, created_at);
'''),(2,r'''
CREATE TABLE workflow_commands (
 run_id TEXT NOT NULL REFERENCES workflow_runs(run_id) ON DELETE CASCADE,
 idempotency_key TEXT NOT NULL,
 expected_state TEXT NOT NULL,
 expected_version INTEGER NOT NULL,
 target_state TEXT NOT NULL,
 actor TEXT NOT NULL,
 result_version INTEGER NOT NULL,
 event_sequence INTEGER NOT NULL,
 created_at TEXT NOT NULL,
 PRIMARY KEY(run_id,idempotency_key)
);
CREATE INDEX idx_workflow_commands_run ON workflow_commands(run_id,created_at);
CREATE UNIQUE INDEX idx_workflow_one_terminal_event ON workflow_events(run_id) WHERE event_type='terminal';
'''),(3,r'''
CREATE TABLE workflow_event_outbox (
 outbox_id INTEGER PRIMARY KEY AUTOINCREMENT,
 run_id TEXT NOT NULL,
 sequence INTEGER NOT NULL,
 payload_json TEXT NOT NULL,
 committed_at TEXT NOT NULL,
 published_at TEXT,
 publish_attempts INTEGER NOT NULL DEFAULT 0 CHECK(publish_attempts>=0),
 UNIQUE(run_id,sequence),
 FOREIGN KEY(run_id,sequence) REFERENCES workflow_events(run_id,sequence) ON DELETE RESTRICT
);
CREATE TABLE conversation_messages (
 message_id TEXT PRIMARY KEY,
 run_id TEXT NOT NULL,
 event_sequence INTEGER NOT NULL,
 role TEXT NOT NULL CHECK(role IN('user','assistant','system')),
 content TEXT NOT NULL CHECK(length(content)<=2000),
 created_at TEXT NOT NULL,
 UNIQUE(run_id,event_sequence),
 FOREIGN KEY(run_id,event_sequence) REFERENCES workflow_events(run_id,sequence) ON DELETE RESTRICT
);
CREATE INDEX idx_outbox_pending ON workflow_event_outbox(published_at,outbox_id);
CREATE INDEX idx_messages_run ON conversation_messages(run_id,event_sequence);
'''),(4,r'''
CREATE TABLE agent_profile_drafts (
 profile_id TEXT PRIMARY KEY,
 revision INTEGER NOT NULL CHECK(revision>0),
 payload_json TEXT NOT NULL,
 updated_at TEXT NOT NULL
);
CREATE TABLE published_agent_profile_versions (
 profile_id TEXT NOT NULL,
 version INTEGER NOT NULL CHECK(version>0),
 content_hash TEXT NOT NULL,
 payload_json TEXT NOT NULL,
 published_at TEXT NOT NULL,
 PRIMARY KEY(profile_id,version),
 UNIQUE(profile_id,content_hash)
);
CREATE TABLE workflow_run_profile_bindings (
 run_id TEXT PRIMARY KEY REFERENCES workflow_runs(run_id) ON DELETE CASCADE,
 profile_id TEXT NOT NULL,
 profile_version INTEGER NOT NULL,
 profile_content_hash TEXT NOT NULL,
 bound_at TEXT NOT NULL,
 FOREIGN KEY(profile_id,profile_version) REFERENCES published_agent_profile_versions(profile_id,version)
);
CREATE INDEX idx_profile_versions_hash ON published_agent_profile_versions(content_hash);
'''),(5,r'''
CREATE TABLE control_plane_commands (
 command_scope TEXT NOT NULL,
 idempotency_key TEXT NOT NULL,
 request_hash TEXT NOT NULL,
 result_json TEXT NOT NULL,
 created_at TEXT NOT NULL,
 PRIMARY KEY(command_scope,idempotency_key)
);
CREATE INDEX idx_control_plane_commands_created ON control_plane_commands(created_at);
'''))

class LegacyReader(Protocol):
 def get_run(self, run_id: str) -> Any: ...
 def list_events(self, run_id: str) -> Sequence[Any]: ...

class DomainDatabase:
 def __init__(self, path: str|Path, *, timeout: float=5.0, migrations: Sequence[tuple[int,str]]=MIGRATIONS):
  self.path=Path(path); self.timeout=timeout; self.migrations=tuple(migrations); self._local=threading.local(); self._lock=threading.RLock()
 def connect(self) -> sqlite3.Connection:
  try:
   self.path.parent.mkdir(parents=True,exist_ok=True)
   db=sqlite3.connect(self.path,timeout=self.timeout,isolation_level=None,check_same_thread=False)
   db.row_factory=sqlite3.Row; db.execute("PRAGMA foreign_keys=ON"); db.execute(f"PRAGMA busy_timeout={int(self.timeout*1000)}"); db.execute("PRAGMA synchronous=FULL"); db.execute("PRAGMA journal_mode=WAL")
   if db.execute("PRAGMA quick_check").fetchone()[0]!="ok": db.close(); raise CorruptDatabase("database integrity check failed")
   return db
  except sqlite3.DatabaseError as exc:
   if db is not None:
    try:db.close()
    except sqlite3.DatabaseError:pass
   raise CorruptDatabase("database could not be opened safely") from exc
 def initialize(self) -> None:
  with self.connect() as db:
   try:
    db.execute("BEGIN EXCLUSIVE"); db.execute("CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
    applied={r[0] for r in db.execute("SELECT version FROM schema_migrations")}
    for version,sql in self.migrations:
     if version not in applied:
      _execute_script(db,sql); db.execute("INSERT INTO schema_migrations VALUES(?,?)",(version,_now()))
    current=max(applied|{v for v,_ in self.migrations},default=0)
    if current>SCHEMA_VERSION: raise CorruptDatabase("database schema is newer than this application")
    db.execute(f"PRAGMA user_version={SCHEMA_VERSION}"); db.commit()
   except BaseException:
    db.rollback(); raise
  self.validate()
 def validate(self) -> None:
  required={"connections","connection_status","model_endpoints","routing_policies","routing_decisions","agent_profiles","agent_profile_versions","workflow_runs","workflow_steps","workflow_events","approvals","artifacts","operation_leases","usage_records"}
  with self.connect() as db:
   names={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
   if not required.issubset(names) or db.execute("PRAGMA foreign_key_check").fetchone() is not None: raise CorruptDatabase("database schema or foreign keys are corrupt")
 @contextmanager
 def transaction(self, mode: str="IMMEDIATE") -> Iterator[sqlite3.Connection]:
  if mode not in {"DEFERRED","IMMEDIATE","EXCLUSIVE"}: raise ValueError("invalid transaction mode")
  db=self.connect()
  try:
   db.execute(f"BEGIN {mode}"); yield db; db.commit()
  except BaseException:
   db.rollback(); raise
  finally: db.close()

class DomainRepository:
 def __init__(self,database:DomainDatabase): self.database=database; database.initialize()
 def save_connection(self,record:Mapping[str,Any]) -> None:
  p=_safe_payload(record); now=_now(); cid=_required(p,"connection_id");
  with self.database.transaction() as db: db.execute("INSERT INTO connections VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(connection_id) DO UPDATE SET enabled=excluded.enabled,payload_json=excluded.payload_json,updated_at=excluded.updated_at",(cid,_required(p,"provider_id"),_required(p,"account_name"),_required(p,"provider_type"),_required(p,"auth_type"),int(bool(p.get("enabled"))),_json(p),now,now))
 def save_status(self,record:Mapping[str,Any]) -> None:
  p=_safe_payload(record); cid=_required(p,"connection_id"); checked=str(p.get("checked_at") or _now())
  with self.database.transaction() as db: db.execute("INSERT INTO connection_status VALUES(?,?,?,?,?,?,?) ON CONFLICT(connection_id) DO UPDATE SET auth_state=excluded.auth_state,transport_status=excluded.transport_status,detected=excluded.detected,checked_at=excluded.checked_at,safe_message=excluded.safe_message,payload_json=excluded.payload_json",(cid,_required(p,"auth_state"),_required(p,"transport_status"),int(bool(p.get("detected"))),checked,p.get("safe_message"),_json(p)))
 def create_run(self,record:Mapping[str,Any]) -> dict[str,Any]:
  p=_safe_payload(record); key=_required(p,"idempotency_key"); run=_required(p,"run_id"); now=_now()
  with self.database.transaction() as db:
   existing=db.execute("SELECT payload_json FROM workflow_runs WHERE idempotency_key=?",(key,)).fetchone()
   if existing:
    old=_decode(existing[0])
    if old.get("run_id")!=run: raise IdempotencyConflict("idempotency key belongs to another run")
    return old
   db.execute("INSERT INTO workflow_runs(run_id,profile_id,profile_version,status,version,idempotency_key,safe_summary,failure_code,payload_json,created_at,updated_at) VALUES(?,?,?,?,0,?,?,?,?,?,?)",(run,p.get("profile_id"),p.get("profile_version"),_required(p,"status"),key,p.get("safe_summary"),p.get("failure_code"),_json(p),now,now))
  return p
 def get_run(self,run_id:str) -> dict[str,Any]|None:
  with self.database.connect() as db:
   row=db.execute("SELECT payload_json,version,status,updated_at FROM workflow_runs WHERE run_id=?",(run_id,)).fetchone()
   if not row:return None
   p=_decode(row[0]); p.update(version=row[1],status=row[2],updated_at=row[3]); return p
 def update_run(self,run_id:str,expected_version:int,changes:Mapping[str,Any]) -> dict[str,Any]:
  safe=_safe_payload(changes); allowed={"status","safe_summary","failure_code"}
  if set(safe)-allowed: raise DomainPersistenceError("run update contains unsupported fields")
  with self.database.transaction() as db:
   row=db.execute("SELECT payload_json FROM workflow_runs WHERE run_id=? AND version=?",(run_id,expected_version)).fetchone()
   if not row: raise StaleRunVersion("run version is stale")
   payload=_decode(row[0]); payload.update(safe); now=_now(); cur=db.execute("UPDATE workflow_runs SET status=?,version=version+1,safe_summary=?,failure_code=?,payload_json=?,updated_at=? WHERE run_id=? AND version=?",(payload.get("status"),payload.get("safe_summary"),payload.get("failure_code"),_json(payload),now,run_id,expected_version))
   if cur.rowcount!=1: raise StaleRunVersion("run version is stale")
  return self.get_run(run_id) or {}
 def append_event(self,event:Mapping[str,Any]) -> dict[str,Any]:
  p=_safe_payload(event); run=_required(p,"run_id"); seq=int(p["sequence"])
  with self.database.transaction() as db:
   if not db.execute("SELECT 1 FROM workflow_runs WHERE run_id=?",(run,)).fetchone(): raise DomainPersistenceError("event run not found")
   expected=db.execute("SELECT COALESCE(MAX(sequence),0)+1 FROM workflow_events WHERE run_id=?",(run,)).fetchone()[0]
   if seq!=expected: raise EventSequenceError(f"expected event sequence {expected}")
   try: db.execute("INSERT INTO workflow_events VALUES(?,?,?,?,?,?,?)",(run,seq,_required(p,"event_id"),_required(p,"event_type"),p.get("safe_message"),_json(p),str(p.get("created_at") or _now())))
   except sqlite3.IntegrityError as exc: raise EventSequenceError("event is duplicate or out of order") from exc
  return p
 def list_events(self,run_id:str) -> tuple[dict[str,Any],...]:
  with self.database.connect() as db: return tuple(_decode(r[0]) for r in db.execute("SELECT payload_json FROM workflow_events WHERE run_id=? ORDER BY sequence",(run_id,)))
 def insert(self,table:str,record:Mapping[str,Any]) -> None:
  """Bounded metadata insert for remaining aggregate tables."""
  p=_safe_payload(record); specs={
   "model_endpoints":("endpoint_id,connection_id,model_id,enabled,payload_json,created_at,updated_at",("endpoint_id","connection_id","model_id")),
   "routing_policies":("policy_id,policy_version,name,payload_json,created_at,updated_at",("policy_id","policy_version","name")),
   "routing_decisions":("decision_id,policy_id,connection_id,status,reason_code,idempotency_key,payload_json,created_at",("decision_id","policy_id","connection_id","status","reason_code","idempotency_key")),
   "agent_profiles":("profile_id,name,active_version,payload_json,created_at,updated_at",("profile_id","name","active_version")),
   "agent_profile_versions":("profile_id,version,connection_id,endpoint_id,adapter_id,payload_json,created_at",("profile_id","version","connection_id","endpoint_id","adapter_id")),
   "workflow_steps":("run_id,step_id,ordinal,status,version,payload_json,created_at,updated_at",("run_id","step_id","ordinal","status","version")),
   "approvals":("approval_id,run_id,step_id,status,action_code,idempotency_key,payload_json,requested_at,resolved_at",("approval_id","run_id","step_id","status","action_code","idempotency_key","requested_at","resolved_at")),
   "artifacts":("artifact_id,run_id,step_id,artifact_type,content_hash,size_bytes,storage_reference,payload_json,created_at",("artifact_id","run_id","step_id","artifact_type","content_hash","size_bytes","storage_reference")),
   "operation_leases":("lease_id,run_id,operation,owner_id,fencing_token,acquired_at,expires_at",("lease_id","run_id","operation","owner_id","fencing_token","acquired_at","expires_at")),
   "usage_records":("usage_id,connection_id,run_id,idempotency_key,input_units,output_units,cost_micros,payload_json,created_at",("usage_id","connection_id","run_id","idempotency_key","input_units","output_units","cost_micros")),}
  if table not in specs: raise ValueError("unsupported table")
  columns,keys=specs[table]; now=_now(); names=columns.split(',')
  vals=[_json(p) if n=="payload_json" else str(p.get(n) or now) if n in {"created_at","updated_at"} else p.get(n) for n in names]
  with self.database.transaction() as db:
   try: db.execute(f"INSERT INTO {table}({columns}) VALUES({','.join('?' for _ in names)})",vals)
   except sqlite3.IntegrityError as exc:
    if p.get("idempotency_key"): raise IdempotencyConflict("idempotency key already used") from exc
    raise DomainPersistenceError("record violates persistence constraints") from exc

class CompatibilityWorkflowRepository:
 """SQLite-first reads with legacy fallback during incremental migration."""
 def __init__(self,primary:DomainRepository,legacy:LegacyReader): self.primary,self.legacy=primary,legacy
 def get_run(self,run_id:str) -> Any:
  return self.primary.get_run(run_id) or self.legacy.get_run(run_id)
 def list_events(self,run_id:str) -> Sequence[Any]:
  events=self.primary.list_events(run_id); return events if events else self.legacy.list_events(run_id)
 def create_run(self,record:Mapping[str,Any]) -> dict[str,Any]: return self.primary.create_run(record)
 def append_event(self,event:Mapping[str,Any]) -> dict[str,Any]: return self.primary.append_event(event)

def _execute_script(db:sqlite3.Connection,script:str)->None:
 statement=""
 for line in script.splitlines():
  statement += line + "\n"
  if sqlite3.complete_statement(statement):
   if statement.strip(): db.execute(statement)
   statement=""
 if statement.strip(): raise DomainPersistenceError("migration contains incomplete SQL")
def _safe_payload(value:Mapping[str,Any]) -> dict[str,Any]:
 def walk(v:Any,key:str="",depth:int=0)->Any:
  if depth>8: raise DomainPersistenceError("payload nesting exceeds limit")
  if key.casefold() not in {"fencing_token","max_input_tokens","max_output_tokens","input_tokens","output_tokens","total_tokens"} and any(x in key.casefold() for x in FORBIDDEN_KEYS): raise DomainPersistenceError("unsafe persistence field")
  if v is None or isinstance(v,(bool,int)): return v
  if isinstance(v,float):
   if not math.isfinite(v): raise DomainPersistenceError("non-finite number")
   return v
  if isinstance(v,str):
   if len(v)>MAX_TEXT or "\x00" in v or any(x in v.casefold() for x in FORBIDDEN_VALUES): raise DomainPersistenceError("unsafe or unbounded text")
   return v
  if isinstance(v,Mapping): return {str(k):walk(x,str(k),depth+1) for k,x in v.items()}
  if isinstance(v,Sequence) and not isinstance(v,(str,bytes,bytearray)): return [walk(x,"",depth+1) for x in v]
  if hasattr(v,"value"): return walk(v.value,key,depth)
  raise DomainPersistenceError("unsupported persistence value")
 out=walk(value)
 if not isinstance(out,dict): raise DomainPersistenceError("payload must be an object")
 if len(_json(out).encode())>MAX_JSON_BYTES: raise DomainPersistenceError("payload exceeds limit")
 return out
def _json(v:Mapping[str,Any])->str:return json.dumps(v,ensure_ascii=True,sort_keys=True,separators=(",",":"))
def _decode(raw:str)->dict[str,Any]:
 try:v=json.loads(raw)
 except Exception as exc:raise CorruptDatabase("stored JSON is corrupt") from exc
 if not isinstance(v,dict):raise CorruptDatabase("stored JSON is not an object")
 return v
def _required(p:Mapping[str,Any],key:str)->str:
 v=p.get(key)
 if not isinstance(v,str) or not v.strip():raise DomainPersistenceError(f"{key} is required")
 return v
def _now()->str:return datetime.now(timezone.utc).isoformat().replace("+00:00","Z")
