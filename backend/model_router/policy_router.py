"""Deterministic policy routing for model calls only."""
from __future__ import annotations
import hashlib,json
from dataclasses import dataclass,field
from enum import Enum
from typing import Any,Iterable,Mapping,Protocol
from backend.state.domain_persistence import DomainDatabase,_json,_now,_safe_payload

class Privacy(str,Enum):PUBLIC="public";SENSITIVE="sensitive";RESTRICTED="restricted"
class FallbackPolicy(str,Enum):NONE="none";SAME_PROVIDER="same_provider";APPROVED_PROVIDER_GROUP="approved_provider_group";ASK_BEFORE_CROSS_PROVIDER="ask_before_cross_provider"
class RouteFailure(str,Enum):NO_ELIGIBLE_MODEL="no_eligible_model";CONSENT_REQUIRED="consent_required"
NON_FALLBACK_ERRORS=frozenset({"authentication_required","authentication_error","auth_error","invalid_credentials","missing_credentials","credential_missing","configuration_error","policy_denied","recipient_not_approved","consent_required","disclosure_denied","unapproved_external_disclosure","fallback_not_authorized"})
@dataclass(frozen=True,slots=True)
class ModelEndpointCandidate:
 endpoint_id:str;connection_id:str;provider_id:str;model_id:str;capabilities:frozenset[str];context_window:int;local:bool;health:str;cost_per_million:int;latency_ms:int;quality:int;provider_group:str|None=None
@dataclass(frozen=True,slots=True)
class ModelRoutingRequest:
 request_id:str;required_capabilities:frozenset[str];context_tokens:int;privacy:Privacy;approved_recipients:frozenset[str];fallback_policy:FallbackPolicy;preferred_provider_id:str|None=None;exact_model_id:str|None=None;local_only:bool=False;max_cost_per_million:int|None=None;consent_granted:bool=False;approved_provider_groups:frozenset[str]=frozenset()
@dataclass(frozen=True,slots=True)
class CandidateRejection:
 endpoint_id:str;reasons:tuple[str,...]
@dataclass(frozen=True,slots=True)
class RoutingDecision:
 decision_id:str;request_id:str;eligible:tuple[str,...];rejected:tuple[CandidateRejection,...];selected_endpoint_id:str|None;selected_provider_id:str|None;selected_model_id:str|None;fallback_chain:tuple[str,...];fallback_policy:FallbackPolicy;consent_granted:bool;failure:RouteFailure|None=None;usage:Mapping[str,Any]=field(default_factory=dict)
 def to_dict(self):return {"decision_id":self.decision_id,"request_id":self.request_id,"eligible":list(self.eligible),"rejected":[{"endpoint_id":x.endpoint_id,"reasons":list(x.reasons)} for x in self.rejected],"selected_endpoint_id":self.selected_endpoint_id,"selected_provider_id":self.selected_provider_id,"selected_model_id":self.selected_model_id,"fallback_chain":list(self.fallback_chain),"fallback_policy":self.fallback_policy.value,"consent_granted":self.consent_granted,"failure":self.failure.value if self.failure else None,"usage":dict(self.usage)}
class DecisionStore(Protocol):
 def save(self,decision:RoutingDecision)->None:...
 def record_usage(self,decision_id:str,usage:Mapping[str,Any])->None:...
class MemoryDecisionStore:
 def __init__(self):self.decisions={};self.usage={}
 def save(self,d):self.decisions.setdefault(d.decision_id,d)
 def record_usage(self,decision_id,usage):self.usage.setdefault(decision_id,dict(usage))
class SQLiteDecisionStore:
 def __init__(self,database:DomainDatabase):self.database=database;database.initialize()
 def save(self,d):
  p=_safe_payload(d.to_dict());now=_now();policy_id=f"model-router.{d.fallback_policy.value}"
  with self.database.transaction() as db:
   db.execute("INSERT INTO routing_policies(policy_id,policy_version,name,payload_json,created_at,updated_at) VALUES(?,1,?,?,?,?) ON CONFLICT(policy_id) DO NOTHING",(policy_id,d.fallback_policy.value,_json({"fallback_policy":d.fallback_policy.value}),now,now))
   db.execute("INSERT INTO routing_decisions(decision_id,policy_id,connection_id,status,reason_code,idempotency_key,payload_json,created_at) VALUES(?,?,NULL,?,?,?,?,?) ON CONFLICT(idempotency_key) DO NOTHING",(d.decision_id,policy_id,"selected" if d.selected_endpoint_id else "rejected",d.failure.value if d.failure else None,d.request_id,_json(p),now))
 def record_usage(self,decision_id,usage):
  p=_safe_payload(dict(usage));now=_now()
  with self.database.transaction() as db:
   row=db.execute("SELECT payload_json FROM routing_decisions WHERE decision_id=?",(decision_id,)).fetchone()
   if not row:raise KeyError(decision_id)
   db.execute("UPDATE routing_decisions SET payload_json=? WHERE decision_id=?",(_json({**json.loads(row[0]),"usage":p}),decision_id))
   db.execute("INSERT INTO usage_records(usage_id,idempotency_key,input_units,output_units,cost_micros,payload_json,created_at) VALUES(?,?,?,?,?,?,?) ON CONFLICT(idempotency_key) DO NOTHING",(f"usage.{decision_id}",f"usage.{decision_id}",int(p.get("input_units",0)),int(p.get("output_units",0)),p.get("cost_micros"),_json(p),now))
class ModelCallPolicyRouter:
 def __init__(self,candidates:Iterable[ModelEndpointCandidate],store:DecisionStore):self.candidates=tuple(candidates);self.store=store
 def decide(self,request:ModelRoutingRequest)->RoutingDecision:
  rejected=[];eligible=[]
  for candidate in self.candidates:
   reasons=[]
   if not request.required_capabilities.issubset(candidate.capabilities):reasons.append("capability_mismatch")
   if candidate.context_window<request.context_tokens:reasons.append("context_too_large")
   if candidate.health not in {"ready","connected"}:reasons.append("unhealthy")
   if (request.local_only or request.privacy is Privacy.RESTRICTED) and not candidate.local:reasons.append("local_only_policy")
   if not candidate.local and candidate.provider_id not in request.approved_recipients:reasons.append("recipient_not_approved")
   if request.privacy is Privacy.SENSITIVE and not candidate.local and not request.consent_granted:reasons.append("consent_required")
   if request.max_cost_per_million is not None and candidate.cost_per_million>request.max_cost_per_million:reasons.append("cost_limit")
   if reasons:rejected.append(CandidateRejection(candidate.endpoint_id,tuple(sorted(reasons))))
   else:eligible.append(candidate)
  eligible.sort(key=lambda c:((0 if request.preferred_provider_id==c.provider_id else 1),(0 if request.exact_model_id==c.model_id else 1),c.cost_per_million,c.latency_ms,-c.quality,c.provider_id,c.model_id,c.endpoint_id))
  selected=eligible[0] if eligible else None
  fallback=[]
  if selected:
   rest=eligible[1:]
   if request.fallback_policy is FallbackPolicy.SAME_PROVIDER:rest=[c for c in rest if c.provider_id==selected.provider_id]
   elif request.fallback_policy is FallbackPolicy.APPROVED_PROVIDER_GROUP:rest=[c for c in rest if c.provider_id==selected.provider_id or (request.consent_granted and selected.provider_group is not None and selected.provider_group in request.approved_provider_groups and c.provider_group==selected.provider_group)]
   elif request.fallback_policy is FallbackPolicy.ASK_BEFORE_CROSS_PROVIDER and not request.consent_granted:rest=[c for c in rest if c.provider_id==selected.provider_id]
   elif request.fallback_policy is FallbackPolicy.NONE:rest=[]
   fallback=[c.endpoint_id for c in rest]
  failure=None if selected else RouteFailure.NO_ELIGIBLE_MODEL
  if not selected and any("consent_required" in r.reasons for r in rejected): failure=RouteFailure.CONSENT_REQUIRED
  digest=hashlib.sha256(json.dumps({"request":request.request_id,"eligible":[c.endpoint_id for c in eligible],"rejected":[(r.endpoint_id,r.reasons) for r in rejected],"selected":selected.endpoint_id if selected else None,"fallback":fallback,"consent":request.consent_granted},sort_keys=True).encode()).hexdigest()[:24]
  decision=RoutingDecision(f"routing.{digest}",request.request_id,tuple(c.endpoint_id for c in eligible),tuple(sorted(rejected,key=lambda r:r.endpoint_id)),selected.endpoint_id if selected else None,selected.provider_id if selected else None,selected.model_id if selected else None,tuple(fallback),request.fallback_policy,request.consent_granted,failure)
  self.store.save(decision);return decision
 def record_usage(self,decision_id:str,usage:Mapping[str,Any])->None:self.store.record_usage(decision_id,usage)
 @staticmethod
 def may_fallback(error_code:str)->bool:return error_code.casefold() not in NON_FALLBACK_ERRORS
