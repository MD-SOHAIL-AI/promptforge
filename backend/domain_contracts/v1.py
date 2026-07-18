from __future__ import annotations
import re
from dataclasses import dataclass, field, fields, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping
SCHEMA_VERSION=1
_ID=re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")
_SECRET=("api_key","apikey","authorization","credential","password","secret","token")
class FailureCode(str,Enum):
 INVALID_CONTRACT="DOMAIN_INVALID_CONTRACT"; INVALID_STATE="DOMAIN_INVALID_STATE"; FORBIDDEN_AUTHORITY="DOMAIN_FORBIDDEN_PROVIDER_AUTHORITY"; UNSUPPORTED_SCHEMA="DOMAIN_UNSUPPORTED_SCHEMA_VERSION"; COMPATIBILITY_ERROR="DOMAIN_COMPATIBILITY_ERROR"
class DomainContractError(ValueError):
 def __init__(self,code:FailureCode,message:str,*,field_name:str|None=None): self.code,self.field_name=code,field_name; super().__init__(message)
 def to_safe_dict(self): return {"code":self.code.value,"message":str(self),"field":self.field_name}
class ProviderType(str,Enum):
 REMOTE_API="remote_api"; LOCAL_CLI="local_cli"; LOCAL_SERVER="local_server"; ENTERPRISE_PROXY="enterprise_proxy"; VERIFIED_TEMPLATE="verified_template"; MANUAL="manual"
class ConnectionAuthType(str,Enum):
 API_KEY="api_key"; OAUTH_PKCE="oauth_pkce"; OAUTH_DEVICE_CODE="oauth_device_code"; CLI_OWNED_SESSION="cli_owned_session"; LOCAL_NO_AUTH="local_no_auth"; ENTERPRISE_PROXY="enterprise_proxy"
class ConnectionStatus(str,Enum): UNKNOWN="unknown"; READY="ready"; DEGRADED="degraded"; OFFLINE="offline"; ERROR="error"
class AgentCapability(str,Enum):
 GENERATE_FILES="generate_files"; MODIFY_FILES="modify_files"; SUGGEST_COMMANDS="suggest_commands"; READ_SELECTED_CONTEXT="read_selected_context"; RUN_IN_SANDBOX="run_in_sandbox"; CREATE_PATCH="create_patch"; CREATE_REVIEW="create_review"
class RoutingDecisionStatus(str,Enum): SELECTED="selected"; REJECTED="rejected"; NO_ELIGIBLE_ROUTE="no_eligible_route"
class WorkflowRunStatus(str,Enum): QUEUED="queued"; RUNNING="running"; AWAITING_APPROVAL="awaiting_approval"; COMPLETED="completed"; FAILED="failed"; CANCELLED="cancelled"
class WorkflowStepStatus(str,Enum): PENDING="pending"; RUNNING="running"; AWAITING_APPROVAL="awaiting_approval"; COMPLETED="completed"; FAILED="failed"; SKIPPED="skipped"
class ApprovalStatus(str,Enum): PENDING="pending"; APPROVED="approved"; REJECTED="rejected"; EXPIRED="expired"; CANCELLED="cancelled"
FORBIDDEN_PROVIDER_AUTHORITIES=frozenset({"active_workspace_mutation","apply","apply_patch","build","run_build","flash","run_flash","monitor","open_monitor","approval","approve","secret_access","read_secrets","credential_access"})
def _now(): return datetime.now(timezone.utc)
def _id(v,n):
 if not isinstance(v,str) or not _ID.fullmatch(v): raise DomainContractError(FailureCode.INVALID_CONTRACT,f"{n} is invalid",field_name=n)
def _schema(v):
 if v!=SCHEMA_VERSION: raise DomainContractError(FailureCode.UNSUPPORTED_SCHEMA,f"unsupported schema version: {v}",field_name="schema_version")
def _utc(v,n):
 if not isinstance(v,datetime) or v.tzinfo is None or v.utcoffset() is None: raise DomainContractError(FailureCode.INVALID_CONTRACT,f"{n} must be timezone-aware",field_name=n)
def _freeze(v):
 if isinstance(v,Mapping): return MappingProxyType({str(k):_freeze(x) for k,x in v.items()})
 if isinstance(v,(list,tuple)): return tuple(_freeze(x) for x in v)
 if isinstance(v,(set,frozenset)): return frozenset(_freeze(x) for x in v)
 return v
def _safe(v,key=""):
 if any(x in key.casefold() for x in _SECRET): return "[REDACTED]"
 if isinstance(v,Enum): return v.value
 if isinstance(v,datetime): return v.astimezone(timezone.utc).isoformat().replace("+00:00","Z")
 if is_dataclass(v): return {f.name:_safe(getattr(v,f.name),f.name) for f in fields(v) if not f.name.startswith("_")}
 if isinstance(v,Mapping): return {str(k):_safe(x,str(k)) for k,x in v.items()}
 if isinstance(v,(tuple,list,set,frozenset)):
  out=[_safe(x) for x in v]; return sorted(out) if isinstance(v,(set,frozenset)) else out
 if isinstance(v,bytes): return "[REDACTED]"
 return v
class SafeSerializable:
 def to_safe_dict(self): return _safe(self)
@dataclass(frozen=True,slots=True)
class ProviderConnection(SafeSerializable):
 connection_id:str; provider_id:str; name:str; provider_type:ProviderType; auth_type:ConnectionAuthType; detected:bool=False; authenticated:bool=False; health:ConnectionStatus=ConnectionStatus.UNKNOWN; enabled:bool=False; policy_eligible:bool=False; production_eligible:bool=False; credential_ref:str|None=field(default=None,repr=False); metadata:Mapping[str,Any]=field(default_factory=dict,repr=False); schema_version:int=SCHEMA_VERSION
 def __post_init__(self):
  _schema(self.schema_version); _id(self.connection_id,"connection_id"); _id(self.provider_id,"provider_id")
  if not self.name.strip(): raise DomainContractError(FailureCode.INVALID_CONTRACT,"name is required")
  if self.production_eligible and not(self.detected and self.authenticated and self.health is ConnectionStatus.READY and self.enabled and self.policy_eligible): raise DomainContractError(FailureCode.INVALID_STATE,"production eligibility requires detection, authentication, ready health, enablement, and policy eligibility")
  if self.auth_type is ConnectionAuthType.LOCAL_NO_AUTH and not self.authenticated: raise DomainContractError(FailureCode.INVALID_STATE,"local_no_auth must be authentication-satisfied")
  object.__setattr__(self,"metadata",_freeze(self.metadata))
@dataclass(frozen=True,slots=True)
class ModelEndpoint(SafeSerializable):
 endpoint_id:str; connection_id:str; model_id:str; base_url:str|None=None; enabled:bool=True; metadata:Mapping[str,Any]=field(default_factory=dict,repr=False); schema_version:int=SCHEMA_VERSION
 def __post_init__(self): _schema(self.schema_version); _id(self.endpoint_id,"endpoint_id"); _id(self.connection_id,"connection_id"); object.__setattr__(self,"metadata",_freeze(self.metadata))
@dataclass(frozen=True,slots=True)
class AgentAdapterDescriptor(SafeSerializable):
 adapter_id:str; provider_id:str; provider_type:ProviderType; capabilities:frozenset[AgentCapability]; authorities:frozenset[str]=frozenset(); schema_version:int=SCHEMA_VERSION
 def __post_init__(self):
  _schema(self.schema_version); _id(self.adapter_id,"adapter_id"); _id(self.provider_id,"provider_id"); forbidden=sorted(set(self.authorities)&FORBIDDEN_PROVIDER_AUTHORITIES)
  if forbidden: raise DomainContractError(FailureCode.FORBIDDEN_AUTHORITY,f"forbidden provider authority: {', '.join(forbidden)}")
  if any(not isinstance(x,AgentCapability) for x in self.capabilities): raise DomainContractError(FailureCode.INVALID_CONTRACT,"invalid capability")
  object.__setattr__(self,"capabilities",frozenset(self.capabilities)); object.__setattr__(self,"authorities",frozenset(self.authorities))
@dataclass(frozen=True,slots=True)
class AgentProfileVersion(SafeSerializable):
 profile_id:str; version:int; adapter_id:str; connection_id:str; model_endpoint_id:str|None=None; settings:Mapping[str,Any]=field(default_factory=dict,repr=False); created_at:datetime=field(default_factory=_now); schema_version:int=SCHEMA_VERSION
 def __post_init__(self):
  _schema(self.schema_version); [_id(getattr(self,n),n) for n in ("profile_id","adapter_id","connection_id")]; _utc(self.created_at,"created_at")
  if self.version<1: raise DomainContractError(FailureCode.INVALID_CONTRACT,"version must be positive")
  object.__setattr__(self,"settings",_freeze(self.settings))
@dataclass(frozen=True,slots=True)
class AgentProfile(SafeSerializable):
 profile_id:str; name:str; versions:tuple[AgentProfileVersion,...]; active_version:int; schema_version:int=SCHEMA_VERSION
 def __post_init__(self):
  _schema(self.schema_version); _id(self.profile_id,"profile_id"); vs=tuple(self.versions); nums=[x.version for x in vs]
  if not vs or any(x.profile_id!=self.profile_id for x in vs) or len(nums)!=len(set(nums)) or self.active_version not in nums: raise DomainContractError(FailureCode.INVALID_STATE,"invalid profile version set")
  object.__setattr__(self,"versions",vs)
@dataclass(frozen=True,slots=True)
class RoutingPolicy(SafeSerializable):
 policy_id:str; name:str; required_capabilities:frozenset[AgentCapability]=frozenset(); allowed_connection_ids:tuple[str,...]=(); production_only:bool=False; schema_version:int=SCHEMA_VERSION
 def __post_init__(self): _schema(self.schema_version); _id(self.policy_id,"policy_id"); [_id(x,"allowed_connection_ids") for x in self.allowed_connection_ids]; object.__setattr__(self,"required_capabilities",frozenset(self.required_capabilities))
@dataclass(frozen=True,slots=True)
class RoutingDecision(SafeSerializable):
 decision_id:str; policy_id:str; status:RoutingDecisionStatus; connection_id:str|None=None; adapter_id:str|None=None; reason_code:str|None=None; considered_connection_ids:tuple[str,...]=(); created_at:datetime=field(default_factory=_now); schema_version:int=SCHEMA_VERSION
 def __post_init__(self):
  _schema(self.schema_version); _id(self.decision_id,"decision_id"); _id(self.policy_id,"policy_id"); _utc(self.created_at,"created_at"); selected=self.status is RoutingDecisionStatus.SELECTED
  if selected!=(self.connection_id is not None and self.adapter_id is not None): raise DomainContractError(FailureCode.INVALID_STATE,"selected decision requires connection and adapter")
  if not selected and not self.reason_code: raise DomainContractError(FailureCode.INVALID_STATE,"unselected decision requires reason")
@dataclass(frozen=True,slots=True)
class WorkflowStep(SafeSerializable):
 step_id:str; name:str; status:WorkflowStepStatus=WorkflowStepStatus.PENDING; adapter_id:str|None=None; failure_code:str|None=None; schema_version:int=SCHEMA_VERSION
 def __post_init__(self):
  _schema(self.schema_version); _id(self.step_id,"step_id")
  if (self.status is WorkflowStepStatus.FAILED)!=(self.failure_code is not None): raise DomainContractError(FailureCode.INVALID_STATE,"failure code must match failed state")
@dataclass(frozen=True,slots=True)
class WorkflowEvent(SafeSerializable):
 event_id:str; run_id:str; sequence:int; event_type:str; safe_message:str; step_id:str|None=None; metadata:Mapping[str,Any]=field(default_factory=dict,repr=False); created_at:datetime=field(default_factory=_now); schema_version:int=SCHEMA_VERSION
 def __post_init__(self): _schema(self.schema_version); _id(self.event_id,"event_id"); _id(self.run_id,"run_id"); _utc(self.created_at,"created_at"); object.__setattr__(self,"metadata",_freeze(self.metadata))
@dataclass(frozen=True,slots=True)
class ApprovalRequest(SafeSerializable):
 approval_id:str; run_id:str; step_id:str; action:str; status:ApprovalStatus=ApprovalStatus.PENDING; requested_at:datetime=field(default_factory=_now); resolved_at:datetime|None=None; schema_version:int=SCHEMA_VERSION
 def __post_init__(self):
  _schema(self.schema_version); [_id(getattr(self,n),n) for n in ("approval_id","run_id","step_id")]; _utc(self.requested_at,"requested_at")
  if self.resolved_at: _utc(self.resolved_at,"resolved_at")
  if (self.status is ApprovalStatus.PENDING)!=(self.resolved_at is None): raise DomainContractError(FailureCode.INVALID_STATE,"approval status and resolution disagree")
@dataclass(frozen=True,slots=True)
class WorkflowRun(SafeSerializable):
 run_id:str; profile_id:str; profile_version:int; status:WorkflowRunStatus; steps:tuple[WorkflowStep,...]; events:tuple[WorkflowEvent,...]=(); approval_requests:tuple[ApprovalRequest,...]=(); created_at:datetime=field(default_factory=_now); schema_version:int=SCHEMA_VERSION
 def __post_init__(self):
  _schema(self.schema_version); _id(self.run_id,"run_id"); _id(self.profile_id,"profile_id"); _utc(self.created_at,"created_at"); ss,es,aps=tuple(self.steps),tuple(self.events),tuple(self.approval_requests); ids={x.step_id for x in ss}
  if any(x.run_id!=self.run_id for x in es+aps) or any(x.step_id not in ids for x in aps): raise DomainContractError(FailureCode.INVALID_STATE,"workflow child ownership mismatch")
  seq=[x.sequence for x in es]
  if seq!=sorted(set(seq)): raise DomainContractError(FailureCode.INVALID_STATE,"event sequences must be unique and increasing")
  if self.status is WorkflowRunStatus.AWAITING_APPROVAL and not any(x.status is ApprovalStatus.PENDING for x in aps): raise DomainContractError(FailureCode.INVALID_STATE,"awaiting approval requires pending request")
  object.__setattr__(self,"steps",ss); object.__setattr__(self,"events",es); object.__setattr__(self,"approval_requests",aps)
