"""Authoritative backend read models and idempotent commands for focused UI surfaces."""
from __future__ import annotations
import hashlib,re
from dataclasses import replace
from typing import Any,Callable
from fastapi import APIRouter,Header,Request
from pydantic import Field
from backend.agent_runtime.agent_profiles import AgentProfileError,AgentProfileService,_draft_from,_draft_payload,_validate_draft
from backend.connection_registry.registry import ConnectionRegistryError
from backend.state.domain_persistence import DomainDatabase,_decode,_json,_now,_safe_payload
from ..errors import APIError
from ..schemas.common import APIModel

router=APIRouter(prefix="/control-plane",tags=["control-plane"])
_KEY=re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
class ConnectionCommand(APIModel):
 provider_id:str|None=None
 account_name:str="default"
 api_key:str|None=Field(default=None,max_length=8192)
 enabled:bool=True
 launch_official_login:bool=False
class ProfileCommand(APIModel):
 profile_id:str|None=None
 source_profile_id:str|None=None
 expected_revision:int|None=None
 draft:dict[str,Any]|None=None
class PolicyCommand(APIModel):values:dict[str,bool|int|str]

def _db(request:Request)->DomainDatabase:
 value=getattr(request.app.state,"workflow_database",None)
 if not isinstance(value,DomainDatabase):raise APIError(503,"CONTROL_PLANE_UNAVAILABLE","Durable control-plane state is unavailable.",{})
 return value
def _registry(request:Request):
 value=getattr(getattr(request.app.state.model_router_service,"registry",None),"connections",None)
 if value is None:raise APIError(503,"CONNECTIONS_UNAVAILABLE","Connection state is unavailable.",{})
 return value
def _command(request:Request,scope:str,key:str|None,body:dict[str,Any],execute:Callable[[],dict[str,Any]]):
 if not key or not _KEY.fullmatch(key):raise APIError(422,"IDEMPOTENCY_KEY_REQUIRED","A valid Idempotency-Key header is required.",{})
 safe=_safe_payload(body);digest=hashlib.sha256(_json(safe).encode()).hexdigest()
 with _db(request).transaction() as db:
  row=db.execute("SELECT request_hash,result_json FROM control_plane_commands WHERE command_scope=? AND idempotency_key=?",(scope,key)).fetchone()
  if row:
   if row["request_hash"]!=digest:raise APIError(409,"IDEMPOTENCY_CONFLICT","The idempotency key was already used for a different command.",{})
   return _decode(row["result_json"])
  result=_safe_payload(execute())
  db.execute("INSERT INTO control_plane_commands VALUES(?,?,?,?,?)",(scope,key,digest,_json(result),_now()))
  return result
def _view(record):
 item=record.to_safe_dict();healthy=item["transport_status"]=="ready"
 item.update(connected=bool(item["detected"] and item["transport_status"] in {"ready","degraded"}),healthy=healthy,policy_eligible=False,routing_eligibility="not_evaluated",production_eligible=False)
 return item

@router.get("/connections")
async def connections(request:Request):return {"schema_version":1,"connections":[_view(x) for x in _registry(request).list()],"authority":"backend"}
@router.post("/connections/discover")
async def discover(request:Request,idempotency_key:str|None=Header(None,alias="Idempotency-Key")):
 registry=_registry(request);return _command(request,"connections.discover",idempotency_key,{},lambda:{"connections":[_view(x) for x in registry.discover()]})
@router.post("/connections/connect")
async def connect(body:ConnectionCommand,request:Request,idempotency_key:str|None=Header(None,alias="Idempotency-Key")):
 if not body.provider_id:raise APIError(422,"PROVIDER_REQUIRED","Provider is required.",{})
 registry=_registry(request)
 try:return _command(request,"connections.connect",idempotency_key,{"provider_id":body.provider_id,"account_name":body.account_name,"enabled":body.enabled,"has_api_key":bool(body.api_key)},lambda:{"connection":_view(registry.connect(body.provider_id or "",account_name=body.account_name,api_key=body.api_key,enabled=body.enabled,launch_official_login=body.launch_official_login))})
 except ConnectionRegistryError as exc:raise APIError(422,exc.code.value,exc.safe_message,{}) from exc
@router.post("/connections/{connection_id}/refresh")
async def refresh(connection_id:str,request:Request,idempotency_key:str|None=Header(None,alias="Idempotency-Key")):
 try:return _command(request,f"connections.refresh:{connection_id}",idempotency_key,{"connection_id":connection_id},lambda:{"connection":_view(_registry(request).refresh_status(connection_id))})
 except ConnectionRegistryError as exc:raise APIError(404,exc.code.value,exc.safe_message,{}) from exc
@router.post("/connections/{connection_id}/disconnect")
async def disconnect(connection_id:str,request:Request,idempotency_key:str|None=Header(None,alias="Idempotency-Key")):
 try:return _command(request,f"connections.disconnect:{connection_id}",idempotency_key,{"connection_id":connection_id},lambda:{"connection":_view(_registry(request).disconnect(connection_id))})
 except ConnectionRegistryError as exc:raise APIError(422,exc.code.value,exc.safe_message,{}) from exc

@router.get("/models-routes")
async def models_routes(request:Request):
 service=request.app.state.model_router_service
 with _db(request).connect() as db:decisions=[_decode(x["payload_json"]) for x in db.execute("SELECT payload_json FROM routing_decisions ORDER BY created_at DESC LIMIT 50")]
 return {"schema_version":1,"providers":[x.to_dict() for x in service.registry.list_providers()],"routes":[x.to_dict() for x in service.registry.list_routes()],"decisions":decisions,"usage":service.usage.list_records(limit=50),"fallback_requires_consent":True,"authority":"backend"}

@router.get("/agent-profiles")
async def profiles(request:Request):
 with _db(request).connect() as db:
  drafts=[_decode(x["payload_json"]) for x in db.execute("SELECT payload_json FROM agent_profile_drafts ORDER BY profile_id")]
  versions=[_decode(x["payload_json"])|{"profile_id":x["profile_id"],"version":x["version"],"content_hash":x["content_hash"],"published_at":x["published_at"]} for x in db.execute("SELECT profile_id,version,content_hash,payload_json,published_at FROM published_agent_profile_versions ORDER BY profile_id,version DESC")]
 return {"schema_version":1,"drafts":drafts,"versions":versions,"authority":"backend"}
def _validated(payload):
 draft=_draft_from(payload);_validate_draft(draft);return {"valid":True,"profile_id":draft.profile_id,"revision":draft.revision}
@router.post("/agent-profiles/validate")
async def validate_profile(body:ProfileCommand,request:Request,idempotency_key:str|None=Header(None,alias="Idempotency-Key")):
 if body.draft is None:raise APIError(422,"PROFILE_DRAFT_REQUIRED","A profile draft is required.",{})
 try:return _command(request,"profiles.validate",idempotency_key,{"draft":body.draft},lambda:_validated(body.draft or {}))
 except (AgentProfileError,KeyError,TypeError) as exc:raise APIError(422,"PROFILE_INVALID",str(exc),{}) from exc
@router.post("/agent-profiles/save")
async def save_profile(body:ProfileCommand,request:Request,idempotency_key:str|None=Header(None,alias="Idempotency-Key")):
 if body.draft is None:raise APIError(422,"PROFILE_DRAFT_REQUIRED","A profile draft is required.",{})
 service=AgentProfileService(_db(request))
 try:return _command(request,"profiles.save",idempotency_key,{"draft":body.draft,"expected_revision":body.expected_revision},lambda:{"draft":_draft_payload(service.save_draft(_draft_from(body.draft or {}),expected_revision=body.expected_revision))})
 except (AgentProfileError,KeyError,TypeError) as exc:raise APIError(409,"PROFILE_SAVE_FAILED",str(exc),{}) from exc
@router.post("/agent-profiles/publish")
async def publish_profile(body:ProfileCommand,request:Request,idempotency_key:str|None=Header(None,alias="Idempotency-Key")):
 if not body.profile_id:raise APIError(422,"PROFILE_REQUIRED","A profile is required.",{})
 service=AgentProfileService(_db(request))
 try:return _command(request,f"profiles.publish:{body.profile_id}",idempotency_key,{"profile_id":body.profile_id},lambda:{"version":service.publish(body.profile_id or "").to_safe_dict()})
 except (AgentProfileError,KeyError) as exc:raise APIError(422,"PROFILE_PUBLISH_FAILED",str(exc),{}) from exc
@router.post("/agent-profiles/clone")
async def clone_profile(body:ProfileCommand,request:Request,idempotency_key:str|None=Header(None,alias="Idempotency-Key")):
 if not body.source_profile_id or not body.profile_id:raise APIError(422,"PROFILE_REQUIRED","Source and destination profile IDs are required.",{})
 service=AgentProfileService(_db(request))
 def execute():
  clone=replace(service.get_draft(body.source_profile_id or ""),profile_id=body.profile_id or "",revision=1)
  return {"draft":_draft_payload(service.save_draft(clone))}
 try:return _command(request,f"profiles.clone:{body.profile_id}",idempotency_key,{"source_profile_id":body.source_profile_id,"profile_id":body.profile_id},execute)
 except (AgentProfileError,KeyError) as exc:raise APIError(422,"PROFILE_CLONE_FAILED",str(exc),{}) from exc

@router.get("/policies")
async def policies(request:Request):
 raw=request.app.state.settings_service.get_settings();values=raw.get("settings",raw) if isinstance(raw,dict) else {}
 selected={k:v for k,v in values.items() if any(m in k for m in ("privacy","context","workspace","approval","flash","hardware")) and isinstance(v,(bool,int,str))}
 with _db(request).connect() as db:approvals=[_decode(x["payload_json"])|{"approval_id":x["approval_id"],"status":x["status"],"action_code":x["action_code"]} for x in db.execute("SELECT approval_id,status,action_code,payload_json FROM approvals WHERE status='pending' ORDER BY requested_at")]
 return {"schema_version":1,"values":selected,"pending_approvals":approvals,"authority":"backend"}
@router.patch("/policies")
async def update_policies(body:PolicyCommand,request:Request,idempotency_key:str|None=Header(None,alias="Idempotency-Key")):
 allowed=("privacy","context","workspace","approval","flash","hardware")
 if any(not any(m in key for m in allowed) for key in body.values):raise APIError(422,"POLICY_FIELD_INVALID","Only privacy, context, workspace, and hardware policy fields are accepted.",{})
 return _command(request,"policies.update",idempotency_key,{"values":body.values},lambda:{"settings":request.app.state.settings_service.patch_settings(body.values)})

@router.get("/runs")
async def runs(request:Request,limit:int=50):
 with _db(request).connect() as db:
  result=[]
  for row in db.execute("SELECT run_id,status,version,safe_summary,failure_code,created_at,updated_at FROM workflow_runs ORDER BY updated_at DESC LIMIT ?",(max(1,min(limit,100)),)):
   run_id=row["run_id"];events=[_decode(x["payload_json"]) for x in db.execute("SELECT payload_json FROM workflow_events WHERE run_id=? ORDER BY sequence DESC LIMIT 20",(run_id,))]
   approvals=[dict(x) for x in db.execute("SELECT approval_id,status,action_code,requested_at,resolved_at FROM approvals WHERE run_id=? ORDER BY requested_at",(run_id,))]
   artifacts=[dict(x) for x in db.execute("SELECT artifact_id,artifact_type,content_hash,size_bytes,storage_reference,created_at FROM artifacts WHERE run_id=? ORDER BY created_at",(run_id,))]
   cost=db.execute("SELECT COALESCE(SUM(cost_micros),0) cost FROM usage_records WHERE run_id=?",(run_id,)).fetchone()["cost"];waiting=row["status"].startswith("awaiting_")
   result.append(dict(row)|{"events":events,"approvals":approvals,"artifacts":artifacts,"cost_micros":cost,"recovery":{"resumable":waiting or row["status"] in {"failed","timed_out"},"requires_approval":waiting and any(x["status"]=="pending" for x in approvals)}})
 return {"schema_version":1,"runs":result,"authority":"backend"}
