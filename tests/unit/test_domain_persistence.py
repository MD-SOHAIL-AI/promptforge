import sqlite3
import threading
from pathlib import Path
from types import SimpleNamespace
import pytest
from backend.state.domain_persistence import (CompatibilityWorkflowRepository,CorruptDatabase,DomainDatabase,DomainPersistenceError,DomainRepository,EventSequenceError,IdempotencyConflict,StaleRunVersion)

def connection(repo):
 repo.save_connection({"connection_id":"openrouter.default","provider_id":"openrouter","account_name":"default","provider_type":"remote_api","auth_type":"api_key","enabled":True})
def run_record(run_id="run-1",key="key-1"):
 return {"run_id":run_id,"status":"queued","idempotency_key":key,"safe_summary":"bounded"}

def test_migration_wal_foreign_keys_and_all_tables(tmp_path):
 db=DomainDatabase(tmp_path/"state.db"); db.initialize()
 with db.connect() as con:
  assert con.execute("PRAGMA journal_mode").fetchone()[0]=="wal"
  assert con.execute("PRAGMA foreign_keys").fetchone()[0]==1
  names={r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
  assert {"connections","connection_status","model_endpoints","routing_policies","routing_decisions","agent_profiles","agent_profile_versions","workflow_runs","workflow_steps","workflow_events","approvals","artifacts","operation_leases","usage_records","control_plane_commands"}<=names
  assert con.execute("PRAGMA user_version").fetchone()[0]==5

def test_failed_migration_rolls_back_completely(tmp_path):
 path=tmp_path/"bad.db"; bad=((1,"CREATE TABLE leaked(value TEXT);\nTHIS IS INVALID;"),)
 with pytest.raises(sqlite3.Error): DomainDatabase(path,migrations=bad).initialize()
 con=sqlite3.connect(path)
 assert con.execute("SELECT name FROM sqlite_master WHERE name='leaked'").fetchone() is None
 assert con.execute("SELECT name FROM sqlite_master WHERE name='schema_migrations'").fetchone() is None
 con.close()

def test_restart_persistence_and_idempotent_create(tmp_path):
 path=tmp_path/"state.db"; first=DomainRepository(DomainDatabase(path)); first.create_run(run_record())
 second=DomainRepository(DomainDatabase(path)); assert second.get_run("run-1")["safe_summary"]=="bounded"
 assert second.create_run(run_record())["run_id"]=="run-1"
 with pytest.raises(IdempotencyConflict): second.create_run(run_record("run-2","key-1"))

def test_optimistic_concurrent_updates_only_one_wins(tmp_path):
 repo=DomainRepository(DomainDatabase(tmp_path/"state.db")); repo.create_run(run_record()); outcomes=[]
 gate=threading.Barrier(2)
 def update(status):
  gate.wait()
  try: repo.update_run("run-1",0,{"status":status}); outcomes.append("ok")
  except StaleRunVersion: outcomes.append("stale")
 threads=[threading.Thread(target=update,args=(s,)) for s in ("running","cancelled")]
 [t.start() for t in threads]; [t.join() for t in threads]
 assert sorted(outcomes)==["ok","stale"] and repo.get_run("run-1")["version"]==1

def test_event_ordering_and_append_only_trigger(tmp_path):
 db=DomainDatabase(tmp_path/"state.db"); repo=DomainRepository(db); repo.create_run(run_record())
 with pytest.raises(EventSequenceError): repo.append_event({"run_id":"run-1","sequence":2,"event_id":"e2","event_type":"started"})
 repo.append_event({"run_id":"run-1","sequence":1,"event_id":"e1","event_type":"started"})
 repo.append_event({"run_id":"run-1","sequence":2,"event_id":"e2","event_type":"finished"})
 assert [e["sequence"] for e in repo.list_events("run-1")]==[1,2]
 with db.transaction() as con:
  with pytest.raises(sqlite3.IntegrityError): con.execute("UPDATE workflow_events SET event_type='changed' WHERE event_id='e1'")

def test_foreign_keys_and_bounded_safe_payloads(tmp_path):
 repo=DomainRepository(DomainDatabase(tmp_path/"state.db"))
 with pytest.raises(DomainPersistenceError): repo.create_run({**run_record(),"raw_prompt":"do everything"})
 with pytest.raises(DomainPersistenceError): repo.create_run({**run_record(),"safe_summary":"x"*2001})
 with pytest.raises(sqlite3.IntegrityError): repo.save_status({"connection_id":"missing.default","auth_state":"authenticated","transport_status":"ready","detected":True})

def test_remaining_aggregate_repositories_and_idempotency(tmp_path):
 repo=DomainRepository(DomainDatabase(tmp_path/"state.db")); connection(repo)
 repo.save_status({"connection_id":"openrouter.default","auth_state":"authenticated","transport_status":"ready","detected":True})
 repo.insert("model_endpoints",{"endpoint_id":"ep","connection_id":"openrouter.default","model_id":"model","enabled":1})
 repo.insert("routing_policies",{"policy_id":"policy","policy_version":1,"name":"Default"})
 repo.insert("routing_decisions",{"decision_id":"decision","policy_id":"policy","connection_id":"openrouter.default","status":"selected","reason_code":None,"idempotency_key":"decision-key"})
 with pytest.raises(IdempotencyConflict): repo.insert("routing_decisions",{"decision_id":"decision-2","policy_id":"policy","connection_id":"openrouter.default","status":"selected","reason_code":None,"idempotency_key":"decision-key"})
 repo.insert("agent_profiles",{"profile_id":"profile","name":"Agent","active_version":1})
 repo.insert("agent_profile_versions",{"profile_id":"profile","version":1,"connection_id":"openrouter.default","endpoint_id":"ep","adapter_id":"adapter"})
 repo.create_run({**run_record(),"profile_id":"profile","profile_version":1})
 repo.insert("workflow_steps",{"run_id":"run-1","step_id":"step","ordinal":0,"status":"pending","version":0})
 repo.insert("approvals",{"approval_id":"approval","run_id":"run-1","step_id":"step","status":"pending","action_code":"apply","idempotency_key":"approval-key","requested_at":"2026-01-01T00:00:00Z","resolved_at":None})
 repo.insert("artifacts",{"artifact_id":"artifact","run_id":"run-1","step_id":"step","artifact_type":"patch","content_hash":"a"*64,"size_bytes":10,"storage_reference":"artifacts/a.patch"})
 repo.insert("operation_leases",{"lease_id":"lease","run_id":"run-1","operation":"apply","owner_id":"worker","fencing_token":1,"acquired_at":"2026-01-01T00:00:00Z","expires_at":"2026-01-01T00:01:00Z"})
 repo.insert("usage_records",{"usage_id":"usage","connection_id":"openrouter.default","run_id":"run-1","idempotency_key":"usage-key","input_units":2,"output_units":3,"cost_micros":4})

def test_compatibility_repository_reads_legacy_only_when_sqlite_empty(tmp_path):
 primary=DomainRepository(DomainDatabase(tmp_path/"state.db")); legacy=SimpleNamespace(get_run=lambda run_id:{"run_id":run_id,"legacy":True},list_events=lambda run_id:({"run_id":run_id,"sequence":1},))
 compat=CompatibilityWorkflowRepository(primary,legacy)
 assert compat.get_run("old")["legacy"] and compat.list_events("old")[0]["sequence"]==1
 primary.create_run(run_record("new","new-key")); assert "legacy" not in compat.get_run("new")

def test_corrupt_database_fails_closed(tmp_path):
 path=tmp_path/"corrupt.db"; path.write_bytes(b"not a sqlite database")
 with pytest.raises(CorruptDatabase): DomainDatabase(path).initialize()
