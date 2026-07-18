"""Idempotent legacy coding-workflow JSONL to durable SQLite migration."""
from __future__ import annotations
import hashlib
from dataclasses import dataclass
from pathlib import Path
from backend.agent_runtime.coding_workflow_store import CodingWorkflowStore
from backend.state.domain_persistence import DomainDatabase,_json,_now,_safe_payload

STATUS={"awaiting_apply":"awaiting_apply_approval","applying":"applying","awaiting_build":"awaiting_build","building":"building","awaiting_flash":"awaiting_flash_approval","flashing":"flashing","awaiting_monitor":"awaiting_monitor","monitoring":"monitoring","repairing":"repairing","completed":"completed","failed":"failed","cancelled":"cancelled","cancelling":"cancelled"}
TERMINAL={"completed","failed","cancelled","timed_out"}
@dataclass(frozen=True,slots=True)
class MigrationReport:
 discovered:int;migrated:int;skipped:int;events:int;profile_id:str;profile_version:int
class LegacyWorkflowMigrator:
 def __init__(self,store:CodingWorkflowStore,database:DomainDatabase):self.store=store;self.database=database;database.initialize()
 def migrate(self,*,profile_id:str="firmware_engineer",profile_version:int|None=None,dry_run:bool=False)->MigrationReport:
  runs=self.store.list_runs(limit=1000)
  with self.database.connect() as db:
   if profile_version is None:
    row=db.execute("SELECT MAX(version) FROM published_agent_profile_versions WHERE profile_id=?",(profile_id,)).fetchone();profile_version=row[0] if row else None
   version_row=db.execute("SELECT content_hash FROM published_agent_profile_versions WHERE profile_id=? AND version=?",(profile_id,profile_version)).fetchone() if profile_version else None
  if not version_row:raise ValueError("published migration profile version is required")
  migrated=skipped=event_count=0
  for run in runs:
   events=self.store.list_events(run.run_id)
   with self.database.connect() as db:exists=db.execute("SELECT 1 FROM workflow_runs WHERE run_id=?",(run.run_id,)).fetchone()
   if exists:skipped+=1;continue
   if dry_run:migrated+=1;event_count+=len(events);continue
   target=STATUS.get(run.status,"failed");now=run.updated_at or _now();payload=_safe_payload({"run_id":run.run_id,"status":target,"migration_source":"coding_workflow_jsonl_v1","legacy_provider_id":run.provider_id,"project_id":run.project_id,"safe_summary":run.safe_summary,"failure_code":run.failure_code})
   with self.database.transaction() as db:
    db.execute("INSERT INTO workflow_runs(run_id,status,version,idempotency_key,safe_summary,failure_code,payload_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",(run.run_id,target,len(events),f"migration:{run.run_id}",run.safe_summary,run.failure_code,_json(payload),run.created_at,now))
    previous="draft"
    for index,event in enumerate(events,1):
     to_state=STATUS.get(event.status,target);terminal=to_state in TERMINAL and index==len(events);event_type="terminal" if terminal else "transition";safe=event.safe_message
     ep=_safe_payload({"event_id":f"{run.run_id}.migration.{index}","run_id":run.run_id,"sequence":index,"event_type":event_type,"from_state":previous,"to_state":to_state,"actor":"forgex.migration","policy_decision":"legacy_jsonl_migration","input_artifact_hashes":[],"output_artifact_hashes":[],"safe_message":safe,"legacy_event_type":event.event_type})
     db.execute("INSERT INTO workflow_events VALUES(?,?,?,?,?,?,?)",(run.run_id,index,ep["event_id"],event_type,safe,_json(ep),event.created_at));db.execute("INSERT INTO workflow_event_outbox(run_id,sequence,payload_json,committed_at,published_at,publish_attempts) VALUES(?,?,?,?,?,0)",(run.run_id,index,_json(ep),event.created_at,event.created_at));db.execute("INSERT INTO conversation_messages VALUES(?,?,?,?,?,?)",(f"{run.run_id}.migration.message.{index}",run.run_id,index,"system",safe or f"Migrated {event.event_type}.",event.created_at));previous=to_state
    db.execute("INSERT INTO workflow_run_profile_bindings VALUES(?,?,?,?,?)",(run.run_id,profile_id,profile_version,version_row[0],now))
    if run.review_id:
     digest=hashlib.sha256(run.review_id.encode()).hexdigest();db.execute("INSERT INTO artifacts(artifact_id,run_id,step_id,artifact_type,content_hash,size_bytes,storage_reference,payload_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)",(f"{run.run_id}.migrated.review",run.run_id,None,"review",digest,0,run.review_id,_json({"migration_source":"coding_workflow_jsonl_v1"}),now))
    db.execute("INSERT INTO control_plane_commands VALUES(?,?,?,?,?)",("migration.legacy_workflow",run.run_id,hashlib.sha256(_json(payload).encode()).hexdigest(),_json({"migrated":True,"run_id":run.run_id}),now))
   migrated+=1;event_count+=len(events)
  return MigrationReport(len(runs),migrated,skipped,event_count,profile_id,int(profile_version))


def main():
 import argparse,json
 parser=argparse.ArgumentParser(description="Migrate legacy coding workflow JSONL into durable SQLite.")
 parser.add_argument("--state-directory",type=Path,required=True);parser.add_argument("--database",type=Path,required=True);parser.add_argument("--profile-id",default="firmware_engineer");parser.add_argument("--profile-version",type=int);parser.add_argument("--dry-run",action="store_true")
 args=parser.parse_args();report=LegacyWorkflowMigrator(CodingWorkflowStore.from_state_directory(args.state_directory),DomainDatabase(args.database)).migrate(profile_id=args.profile_id,profile_version=args.profile_version,dry_run=args.dry_run)
 print(json.dumps(report.__dict__ if hasattr(report,"__dict__") else {name:getattr(report,name) for name in report.__slots__},indent=2,sort_keys=True))
if __name__=="__main__":main()

