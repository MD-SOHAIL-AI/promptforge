import json
from dataclasses import FrozenInstanceError
import pytest
from backend.agent_runtime.agent_profiles import (AgentProfileError,AgentProfileService,builtin_profile_drafts)
from backend.state.domain_persistence import DomainDatabase,DomainRepository

def service(tmp_path):return AgentProfileService(DomainDatabase(tmp_path/"profiles.db"))
def draft():return builtin_profile_drafts()[0]

def test_draft_edit_and_publish_immutable_versions(tmp_path):
 svc=service(tmp_path);saved=svc.save_draft(draft());v1=svc.publish(saved.profile_id);same=svc.publish(saved.profile_id)
 assert v1.version==same.version==1 and v1.content_hash==same.content_hash
 with pytest.raises(FrozenInstanceError):v1.role="changed"
 edited=svc.get_draft(saved.profile_id);edited.role="Senior firmware engineer";saved2=svc.save_draft(edited,expected_revision=saved.revision);v2=svc.publish(saved2.profile_id)
 assert v2.version==2 and v2.content_hash!=v1.content_hash and svc.get_version(saved.profile_id,1).role==v1.role

def test_forbidden_capabilities_and_safety_overrides_fail_closed(tmp_path):
 svc=service(tmp_path);item=draft();item.capabilities.add("apply")
 with pytest.raises(AgentProfileError):svc.save_draft(item)
 item=draft();item.safety_overrides["active_workspace_mutation"]=True
 with pytest.raises(AgentProfileError):svc.save_draft(item)

def test_secret_free_export_import_and_checksum(tmp_path):
 svc=service(tmp_path);svc.save_draft(draft());published=svc.publish("firmware_engineer");raw=svc.export_version(published.profile_id,published.version)
 assert "api_key" not in raw.casefold() and "password=" not in raw.casefold()
 imported=svc.import_draft(raw,new_profile_id="imported_firmware");assert imported.profile_id=="imported_firmware" and imported.role==published.role
 envelope=json.loads(raw);envelope["document"]["profile"]["system_instructions"]="token=raw"
 with pytest.raises(AgentProfileError):svc.import_draft(json.dumps(envelope))
 envelope=json.loads(raw);envelope["sha256"]="0"*64
 with pytest.raises(AgentProfileError):svc.import_draft(json.dumps(envelope))

def test_run_reproducibility_binds_exact_published_version(tmp_path):
 db=DomainDatabase(tmp_path/"profiles.db");svc=AgentProfileService(db);saved=svc.save_draft(draft());v1=svc.publish(saved.profile_id)
 repo=DomainRepository(db);repo.create_run({"run_id":"run-1","status":"draft","idempotency_key":"run-key"});binding=svc.bind_run("run-1",v1.profile_id,v1.version)
 edited=svc.get_draft(v1.profile_id);edited.system_instructions += " Prefer minimal changes.";svc.save_draft(edited,expected_revision=saved.revision);v2=svc.publish(v1.profile_id)
 reproduced=svc.profile_for_run("run-1");assert reproduced.version==1 and reproduced.content_hash==binding.profile_content_hash and reproduced.system_instructions==v1.system_instructions and v2.version==2
 with pytest.raises(AgentProfileError):svc.bind_run("run-1",v2.profile_id,v2.version)

def test_builtin_firmware_and_build_repair_profiles_validate(tmp_path):
 svc=service(tmp_path);drafts=builtin_profile_drafts();assert {x.profile_id for x in drafts}=={"firmware_engineer","build_repair"}
 versions=[]
 for item in drafts:svc.save_draft(item);versions.append(svc.publish(item.profile_id))
 assert all(v.verification_criteria and v.budgets.max_cost_micros>0 for v in versions)
