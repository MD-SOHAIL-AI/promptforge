from pathlib import Path
import pytest
from backend.agent_runtime.agent_profiles import AgentProfileService,builtin_profile_drafts
from backend.agent_runtime.coding_workflow_store import CodingWorkflowEventRecord,CodingWorkflowRunRecord,CodingWorkflowStore
from backend.migrations.compatibility_cutover import LegacyWorkflowMigrator
from backend.release.cutover_audit import audit,inventory
from backend.state.domain_persistence import DomainDatabase

def published_profile(database):
 service=AgentProfileService(database);draft=builtin_profile_drafts()[0];service.save_draft(draft);return service.publish(draft.profile_id)

def test_legacy_jsonl_workflow_migrates_idempotently_with_profile_and_events(tmp_path):
 state=tmp_path/"legacy";store=CodingWorkflowStore.from_state_directory(state)
 run=CodingWorkflowRunRecord(run_id="legacy-run",provider_id="fake_api",provider_type="api",status="completed",generation_status="succeeded",review_id="review-1",safe_summary="Completed safely.")
 event=CodingWorkflowEventRecord(event_id="legacy-event",run_id=run.run_id,sequence=1,event_type="workflow.completed",stage="verification",status="completed",safe_message="Completed safely.")
 store.persist_run(run,(event,));database=DomainDatabase(tmp_path/"domain.db");profile=published_profile(database);migrator=LegacyWorkflowMigrator(store,database)
 report=migrator.migrate(profile_id=profile.profile_id,profile_version=profile.version)
 assert (report.migrated,report.events)==(1,1)
 with database.connect() as db:
  row=db.execute("SELECT status,version FROM workflow_runs WHERE run_id='legacy-run'").fetchone();assert tuple(row)==("completed",1)
  binding=db.execute("SELECT profile_id,profile_version FROM workflow_run_profile_bindings WHERE run_id='legacy-run'").fetchone();assert tuple(binding)==(profile.profile_id,profile.version)
  assert db.execute("SELECT event_type FROM workflow_events WHERE run_id='legacy-run'").fetchone()[0]=="terminal"
  assert db.execute("SELECT artifact_type FROM artifacts WHERE run_id='legacy-run'").fetchone()[0]=="review"
 assert migrator.migrate(profile_id=profile.profile_id,profile_version=profile.version).skipped==1

def test_cutover_audit_reports_callers_and_blocks_unproven_parity(tmp_path):
 (tmp_path/"backend/api/routes").mkdir(parents=True);(tmp_path/"backend/api/routes/agent_workspace.py").write_text("Executor parity is not production eligible")
 (tmp_path/"backend/legacy.py").write_text("ProductProviderRegistry()\nCodingWorkflowStore.from_state_directory(root)")
 database=DomainDatabase(tmp_path/"domain.db");database.initialize();report=audit(tmp_path,database)
 assert report.releasable is False
 assert "backend/legacy.py" in report.callers["duplicate_registries"]
 assert next(x for x in report.gates if x.name=="agent_runtime_parity").passed is False

def test_inventory_proves_codex_model_provider_absent_from_production_tree():
 root=Path(__file__).resolve().parents[2];callers=inventory(root)
 assert callers["codex_model_routing"]==()
