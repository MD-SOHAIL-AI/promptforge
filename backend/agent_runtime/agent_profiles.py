"""Versioned, secret-free Agent Profiles with immutable publication."""
from __future__ import annotations
import hashlib,json,re
from dataclasses import asdict,dataclass,field,replace
from types import MappingProxyType
from typing import Any,Mapping
from backend.model_router.policy_router import FallbackPolicy,Privacy
from backend.state.domain_persistence import DomainDatabase,DomainPersistenceError,_decode,_json,_now,_safe_payload
from backend.state.durable_workflow import WorkflowState

PROFILE_SCHEMA="forgex.agent_profile.v1";MAX_INSTRUCTIONS=20_000;MAX_CRITERIA=50
BASE_SAFETY_DENIALS=frozenset({"apply","build","flash","monitor","approve","active_workspace_mutation","secret_access","execute_shell","execute_model_suggested_shell"})
SAFE_CAPABILITIES=frozenset({"generate_files","modify_files","read_approved_context","suggest_commands","create_patch","create_review","validate_proposal","repair_proposal"})
_SECRET_KEYS=("api_key","apikey","authorization","credential","password","secret","token")
_SECRET_VALUES=("-----begin private key-----","password=","secret=","token=","api_key=","authorization: bearer")
_ID=re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")
class AgentProfileError(ValueError):pass
@dataclass(frozen=True,slots=True)
class ProfileIdentity:
 display_name:str;description:str;icon:str|None=None
@dataclass(frozen=True,slots=True)
class ContextPolicy:
 privacy:Privacy=Privacy.SENSITIVE;max_files:int=20;max_file_bytes:int=32*1024;max_total_bytes:int=128*1024;require_consent:bool=True
@dataclass(frozen=True,slots=True)
class ModelRoutingPolicy:
 required_capabilities:frozenset[str];fallback_policy:FallbackPolicy=FallbackPolicy.NONE;approved_recipients:frozenset[str]=frozenset();approved_provider_groups:frozenset[str]=frozenset();local_only:bool=False;exact_model_id:str|None=None;max_cost_per_million:int|None=None
@dataclass(frozen=True,slots=True)
class ProfileBudgets:
 max_seconds:int;max_steps:int;max_input_tokens:int;max_output_tokens:int;max_cost_micros:int
@dataclass(frozen=True,slots=True)
class ApprovalPolicy:
 required_for:frozenset[str]=frozenset({"apply","flash"});allow_session_approval:bool=False
@dataclass(slots=True)
class AgentProfileDraft:
 profile_id:str;identity:ProfileIdentity;role:str;system_instructions:str;capabilities:set[str];context_policy:ContextPolicy;model_routing_policy:ModelRoutingPolicy;budgets:ProfileBudgets;approval_policy:ApprovalPolicy;workflow_template:list[str];verification_criteria:list[str];safety_overrides:dict[str,bool]=field(default_factory=dict);revision:int=1;schema_version:str=PROFILE_SCHEMA
@dataclass(frozen=True,slots=True)
class AgentProfileVersion:
 profile_id:str;version:int;identity:ProfileIdentity;role:str;system_instructions:str;capabilities:frozenset[str];context_policy:ContextPolicy;model_routing_policy:ModelRoutingPolicy;budgets:ProfileBudgets;approval_policy:ApprovalPolicy;workflow_template:tuple[str,...];verification_criteria:tuple[str,...];content_hash:str;published_at:str;schema_version:str=PROFILE_SCHEMA
 def to_safe_dict(self)->dict[str,Any]:return _version_payload(self,include_publication=True)
@dataclass(frozen=True,slots=True)
class RunProfileBinding:
 run_id:str;profile_id:str;profile_version:int;profile_content_hash:str;bound_at:str

class AgentProfileService:
 def __init__(self,database:DomainDatabase):self.database=database;database.initialize()
 def save_draft(self,draft:AgentProfileDraft,*,expected_revision:int|None=None)->AgentProfileDraft:
  _validate_draft(draft);payload=_draft_payload(draft);now=_now()
  with self.database.transaction() as db:
   row=db.execute("SELECT revision FROM agent_profile_drafts WHERE profile_id=?",(draft.profile_id,)).fetchone()
   if expected_revision is not None and (not row or row[0]!=expected_revision):raise AgentProfileError("draft revision is stale")
   revision=(row[0]+1) if row else 1;payload["revision"]=revision
   db.execute("INSERT INTO agent_profile_drafts VALUES(?,?,?,?) ON CONFLICT(profile_id) DO UPDATE SET revision=excluded.revision,payload_json=excluded.payload_json,updated_at=excluded.updated_at",(draft.profile_id,revision,_json(_safe_payload(payload)),now))
  return _draft_from(payload)
 def get_draft(self,profile_id:str)->AgentProfileDraft:
  with self.database.connect() as db:row=db.execute("SELECT payload_json FROM agent_profile_drafts WHERE profile_id=?",(profile_id,)).fetchone()
  if not row:raise KeyError(profile_id)
  return _draft_from(_decode(row[0]))
 def publish(self,profile_id:str)->AgentProfileVersion:
  draft=self.get_draft(profile_id);_validate_draft(draft);base=_draft_payload(draft);base.pop("revision",None);canonical=_json(base);digest=hashlib.sha256(canonical.encode()).hexdigest();now=_now()
  with self.database.transaction() as db:
   existing=db.execute("SELECT version,published_at FROM published_agent_profile_versions WHERE profile_id=? AND content_hash=?",(profile_id,digest)).fetchone()
   if existing:return _version_from(base,existing[0],digest,existing[1])
   version=db.execute("SELECT COALESCE(MAX(version),0)+1 FROM published_agent_profile_versions WHERE profile_id=?",(profile_id,)).fetchone()[0]
   published=_version_from(base,version,digest,now);db.execute("INSERT INTO published_agent_profile_versions VALUES(?,?,?,?,?)",(profile_id,version,digest,_json(_version_payload(published,include_publication=False)),now))
  return published
 def get_version(self,profile_id:str,version:int)->AgentProfileVersion:
  with self.database.connect() as db:row=db.execute("SELECT content_hash,payload_json,published_at FROM published_agent_profile_versions WHERE profile_id=? AND version=?",(profile_id,version)).fetchone()
  if not row:raise KeyError((profile_id,version))
  return _version_from(_decode(row[1]),version,row[0],row[2])
 def bind_run(self,run_id:str,profile_id:str,version:int)->RunProfileBinding:
  profile=self.get_version(profile_id,version);now=_now()
  with self.database.transaction() as db:
   if not db.execute("SELECT 1 FROM workflow_runs WHERE run_id=?",(run_id,)).fetchone():raise AgentProfileError("workflow run not found")
   existing=db.execute("SELECT profile_id,profile_version,profile_content_hash,bound_at FROM workflow_run_profile_bindings WHERE run_id=?",(run_id,)).fetchone()
   if existing:
    if (existing[0],existing[1])!=(profile_id,version):raise AgentProfileError("run profile binding is immutable")
    return RunProfileBinding(run_id,*existing)
   db.execute("INSERT INTO workflow_run_profile_bindings VALUES(?,?,?,?,?)",(run_id,profile_id,version,profile.content_hash,now))
  return RunProfileBinding(run_id,profile_id,version,profile.content_hash,now)
 def profile_for_run(self,run_id:str)->AgentProfileVersion:
  with self.database.connect() as db:row=db.execute("SELECT profile_id,profile_version FROM workflow_run_profile_bindings WHERE run_id=?",(run_id,)).fetchone()
  if not row:raise KeyError(run_id)
  return self.get_version(row[0],row[1])
 def export_version(self,profile_id:str,version:int)->str:
  profile=self.get_version(profile_id,version);document={"export_schema":"forgex.agent_profile_export.v1","profile":profile.to_safe_dict()};body=_json(_safe_payload(document));checksum=hashlib.sha256(body.encode()).hexdigest();return json.dumps({"document":document,"sha256":checksum},ensure_ascii=True,sort_keys=True,separators=(",",":"))
 def import_draft(self,raw:str,*,new_profile_id:str|None=None)->AgentProfileDraft:
  if len(raw)>128*1024:raise AgentProfileError("profile import exceeds limit")
  try:envelope=json.loads(raw)
  except json.JSONDecodeError as exc:raise AgentProfileError("profile import is invalid JSON") from exc
  if set(envelope)!={"document","sha256"}:raise AgentProfileError("profile import envelope is invalid")
  document=_safe_import(envelope["document"]);body=_json(document)
  if not isinstance(envelope["sha256"],str) or hashlib.sha256(body.encode()).hexdigest()!=envelope["sha256"]:raise AgentProfileError("profile import checksum mismatch")
  if document.get("export_schema")!="forgex.agent_profile_export.v1":raise AgentProfileError("profile export schema is unsupported")
  p=dict(document["profile"]);p.pop("version",None);p.pop("content_hash",None);p.pop("published_at",None);p["profile_id"]=new_profile_id or p["profile_id"];p["revision"]=1
  draft=_draft_from(p);_validate_draft(draft);return self.save_draft(draft)

def builtin_profile_drafts()->tuple[AgentProfileDraft,...]:
 common_context=ContextPolicy();approval=ApprovalPolicy();
 firmware=AgentProfileDraft("firmware_engineer",ProfileIdentity("Firmware Engineer","Creates bounded review-only embedded firmware proposals."),"Embedded firmware engineer","Produce review-only firmware proposals. Respect board constraints and verification evidence.",{"generate_files","modify_files","read_approved_context","create_review","validate_proposal"},common_context,ModelRoutingPolicy(frozenset({"code_generation","structured_output"}),FallbackPolicy.ASK_BEFORE_CROSS_PROVIDER),ProfileBudgets(300,20,100000,20000,500000),approval,["preparing_context","routing","generating","validating","awaiting_review","awaiting_apply_approval","verifying"],["proposal parses","declared files match authoritative diff","active workspace unchanged"])
 repair=AgentProfileDraft("build_repair",ProfileIdentity("Build Repair","Diagnoses build evidence and proposes bounded repairs."),"Build repair specialist","Use sanitized build diagnostics to create a minimal review-only repair proposal.",{"modify_files","read_approved_context","create_review","validate_proposal","repair_proposal"},common_context,ModelRoutingPolicy(frozenset({"code_generation","repair"}),FallbackPolicy.ASK_BEFORE_CROSS_PROVIDER),ProfileBudgets(240,12,60000,12000,300000),approval,["preparing_context","routing","generating","validating","repairing","awaiting_review","verifying"],["build failure evidence addressed","no undeclared files","active workspace unchanged"])
 return firmware,repair

def _validate_draft(d):
 if d.schema_version!=PROFILE_SCHEMA or not _ID.fullmatch(d.profile_id):raise AgentProfileError("profile identity is invalid")
 if not d.role.strip() or not d.system_instructions.strip() or len(d.system_instructions)>MAX_INSTRUCTIONS:raise AgentProfileError("role or system instructions are invalid")
 if _has_secret(d.system_instructions) or not set(d.capabilities)<=SAFE_CAPABILITIES:raise AgentProfileError("profile contains forbidden capability or secret")
 if any(k in BASE_SAFETY_DENIALS and v for k,v in d.safety_overrides.items()):raise AgentProfileError("profile cannot override base safety denials")
 if d.context_policy.max_files<1 or d.context_policy.max_files>100 or d.context_policy.max_file_bytes<1 or d.context_policy.max_total_bytes<d.context_policy.max_file_bytes:raise AgentProfileError("context policy is invalid")
 b=d.budgets
 if min(b.max_seconds,b.max_steps,b.max_input_tokens,b.max_output_tokens,b.max_cost_micros)<1:raise AgentProfileError("budgets must be positive")
 if not d.workflow_template or any(x not in {s.value for s in WorkflowState} for x in d.workflow_template):raise AgentProfileError("workflow template is invalid")
 if not d.verification_criteria or len(d.verification_criteria)>MAX_CRITERIA or any(_has_secret(x) for x in d.verification_criteria):raise AgentProfileError("verification criteria are invalid")
 _safe_import(_draft_payload(d))
def _draft_payload(d):return {"schema_version":d.schema_version,"profile_id":d.profile_id,"identity":asdict(d.identity),"role":d.role,"system_instructions":d.system_instructions,"capabilities":sorted(d.capabilities),"context_policy":{**asdict(d.context_policy),"privacy":d.context_policy.privacy.value},"model_routing_policy":{**asdict(d.model_routing_policy),"required_capabilities":sorted(d.model_routing_policy.required_capabilities),"fallback_policy":d.model_routing_policy.fallback_policy.value,"approved_recipients":sorted(d.model_routing_policy.approved_recipients),"approved_provider_groups":sorted(d.model_routing_policy.approved_provider_groups)},"budgets":asdict(d.budgets),"approval_policy":{**asdict(d.approval_policy),"required_for":sorted(d.approval_policy.required_for)},"workflow_template":list(d.workflow_template),"verification_criteria":list(d.verification_criteria),"safety_overrides":dict(d.safety_overrides),"revision":d.revision}
def _version_payload(v,include_publication):
 p={"schema_version":v.schema_version,"profile_id":v.profile_id,"identity":asdict(v.identity),"role":v.role,"system_instructions":v.system_instructions,"capabilities":sorted(v.capabilities),"context_policy":{**asdict(v.context_policy),"privacy":v.context_policy.privacy.value},"model_routing_policy":{**asdict(v.model_routing_policy),"required_capabilities":sorted(v.model_routing_policy.required_capabilities),"fallback_policy":v.model_routing_policy.fallback_policy.value,"approved_recipients":sorted(v.model_routing_policy.approved_recipients),"approved_provider_groups":sorted(v.model_routing_policy.approved_provider_groups)},"budgets":asdict(v.budgets),"approval_policy":{**asdict(v.approval_policy),"required_for":sorted(v.approval_policy.required_for)},"workflow_template":list(v.workflow_template),"verification_criteria":list(v.verification_criteria),"safety_overrides":{}}
 if include_publication:p.update(version=v.version,content_hash=v.content_hash,published_at=v.published_at)
 return p
def _draft_from(p):
 c=p["context_policy"];r=p["model_routing_policy"];a=p["approval_policy"]
 return AgentProfileDraft(p["profile_id"],ProfileIdentity(**p["identity"]),p["role"],p["system_instructions"],set(p["capabilities"]),ContextPolicy(Privacy(c["privacy"]),c["max_files"],c["max_file_bytes"],c["max_total_bytes"],c["require_consent"]),ModelRoutingPolicy(frozenset(r["required_capabilities"]),FallbackPolicy(r["fallback_policy"]),frozenset(r["approved_recipients"]),frozenset(r["approved_provider_groups"]),r["local_only"],r.get("exact_model_id"),r.get("max_cost_per_million")),ProfileBudgets(**p["budgets"]),ApprovalPolicy(frozenset(a["required_for"]),a["allow_session_approval"]),list(p["workflow_template"]),list(p["verification_criteria"]),dict(p.get("safety_overrides",{})),int(p.get("revision",1)),p["schema_version"])
def _version_from(p,version,digest,published):
 d=_draft_from({**p,"revision":1});return AgentProfileVersion(d.profile_id,version,d.identity,d.role,d.system_instructions,frozenset(d.capabilities),d.context_policy,d.model_routing_policy,d.budgets,d.approval_policy,tuple(d.workflow_template),tuple(d.verification_criteria),digest,published,d.schema_version)
def _safe_import(v,key="",depth=0):
 if depth>8:raise AgentProfileError("profile is too deeply nested")
 if _secret_key(key):raise AgentProfileError("profile contains a secret field")
 if v is None or isinstance(v,(bool,int)):return v
 if isinstance(v,str):
  if len(v)>MAX_INSTRUCTIONS or _has_secret(v):raise AgentProfileError("profile contains unsafe text")
  return v
 if isinstance(v,list):return [_safe_import(x,"",depth+1) for x in v]
 if isinstance(v,dict):return {str(k):_safe_import(x,str(k),depth+1) for k,x in v.items()}
 raise AgentProfileError("profile contains unsupported data")
def _secret_key(key):
 k=key.casefold();return k in _SECRET_KEYS or k.endswith(("_api_key","_password","_secret","_credential","_access_token","_refresh_token"))
def _has_secret(v):return any(x in v.casefold() for x in _SECRET_VALUES)
