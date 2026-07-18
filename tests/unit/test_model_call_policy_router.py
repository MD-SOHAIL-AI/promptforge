import json
from backend.connection_registry import AuthState
from backend.connection_registry.registry import ConnectionRegistry
from backend.model_router.credentials import MemoryCredentialStore
from backend.model_router.policy_router import (FallbackPolicy,MemoryDecisionStore,ModelCallPolicyRouter,ModelEndpointCandidate,ModelRoutingRequest,Privacy,SQLiteDecisionStore)
from backend.model_router.registry import ProviderRegistry
from backend.model_router.storage import ProviderSettingsStorage
from backend.state.domain_persistence import DomainDatabase

def endpoint(id,provider,model,*,caps=("chat",),context=10000,local=False,health="ready",cost=100,latency=100,quality=80,group="approved"):
 return ModelEndpointCandidate(id,f"{provider}.default",provider,model,frozenset(caps),context,local,health,cost,latency,quality,group)
def request(**kw):
 values=dict(request_id="request-1",required_capabilities=frozenset({"chat"}),context_tokens=1000,privacy=Privacy.PUBLIC,approved_recipients=frozenset({"openai","openrouter"}),fallback_policy=FallbackPolicy.NONE)
 values.update(kw);return ModelRoutingRequest(**values)

def test_capability_context_health_and_cost_filtering():
 candidates=[endpoint("missing","openai","a",caps=("embed",)),endpoint("small","openai","b",context=10),endpoint("sick","openai","c",health="offline"),endpoint("costly","openai","d",cost=500),endpoint("ok","openai","e",cost=50)]
 decision=ModelCallPolicyRouter(candidates,MemoryDecisionStore()).decide(request(max_cost_per_million=100))
 assert decision.selected_endpoint_id=="ok"
 reasons={x.endpoint_id:x.reasons for x in decision.rejected};assert "capability_mismatch" in reasons["missing"] and "context_too_large" in reasons["small"] and "unhealthy" in reasons["sick"] and "cost_limit" in reasons["costly"]

def test_restricted_and_local_only_never_disclose_remotely():
 candidates=[endpoint("remote","openai","remote"),endpoint("local","ollama","local",local=True,cost=0)]
 decision=ModelCallPolicyRouter(candidates,MemoryDecisionStore()).decide(request(privacy=Privacy.RESTRICTED,approved_recipients=frozenset({"openai"})))
 assert decision.selected_endpoint_id=="local" and any(x.endpoint_id=="remote" and "local_only_policy" in x.reasons for x in decision.rejected)

def test_unapproved_recipient_is_rejected_even_when_cheaper():
 candidates=[endpoint("external","other","cheap",cost=0),endpoint("approved","openai","safe",cost=100)]
 decision=ModelCallPolicyRouter(candidates,MemoryDecisionStore()).decide(request(approved_recipients=frozenset({"openai"})))
 assert decision.selected_endpoint_id=="approved" and decision.rejected[0].reasons==("recipient_not_approved",)

def test_cross_provider_fallback_requires_consent():
 candidates=[endpoint("primary","openai","a",cost=10),endpoint("same","openai","b",cost=20),endpoint("cross","openrouter","c",cost=30)]
 router=ModelCallPolicyRouter(candidates,MemoryDecisionStore())
 denied=router.decide(request(fallback_policy=FallbackPolicy.ASK_BEFORE_CROSS_PROVIDER,preferred_provider_id="openai",consent_granted=False));assert denied.fallback_chain==("same",)
 allowed=router.decide(request(request_id="request-2",fallback_policy=FallbackPolicy.ASK_BEFORE_CROSS_PROVIDER,preferred_provider_id="openai",consent_granted=True));assert allowed.fallback_chain==("same","cross")

def test_fallback_policy_groups_and_exact_model_preference():
 candidates=[endpoint("x","openai","exact",cost=100,group="g"),endpoint("cheap","openrouter","other",cost=1,group="other"),endpoint("g2","openrouter","exact",cost=200,group="g")]
 exact=ModelCallPolicyRouter(candidates,MemoryDecisionStore()).decide(request(exact_model_id="exact",fallback_policy=FallbackPolicy.APPROVED_PROVIDER_GROUP,approved_provider_groups=frozenset({"g"}),consent_granted=True))
 assert exact.selected_endpoint_id=="x" and exact.fallback_chain==("g2",)
 denied=ModelCallPolicyRouter(candidates,MemoryDecisionStore()).decide(request(request_id="group-no-consent",exact_model_id="exact",fallback_policy=FallbackPolicy.APPROVED_PROVIDER_GROUP,approved_provider_groups=frozenset({"g"}),consent_granted=False))
 assert denied.selected_endpoint_id=="x" and denied.fallback_chain==()

def test_auth_and_policy_errors_never_fallback():
 router=ModelCallPolicyRouter([],MemoryDecisionStore())
 for code in ("authentication_required","auth_error","invalid_credentials","policy_denied","unapproved_external_disclosure"):assert not router.may_fallback(code)
 assert router.may_fallback("timeout")

def test_decision_is_deterministic_and_persisted_with_usage(tmp_path):
 db=DomainDatabase(tmp_path/"routing.db");store=SQLiteDecisionStore(db);router=ModelCallPolicyRouter([endpoint("b","openai","m",cost=10),endpoint("a","openai","m",cost=10)],store);req=request()
 one=router.decide(req);two=router.decide(req);assert one==two and one.selected_endpoint_id=="a"
 router.record_usage(one.decision_id,{"input_units":10,"output_units":5,"cost_micros":7})
 with db.connect() as con:
  payload=json.loads(con.execute("SELECT payload_json FROM routing_decisions").fetchone()[0]);assert payload["selected_endpoint_id"]=="a" and payload["usage"]["cost_micros"]==7

def test_codex_is_not_a_model_provider_but_remains_cli_owned_connection(tmp_path):
 legacy=ProviderRegistry(ProviderSettingsStorage(tmp_path/"providers.json",credential_store=MemoryCredentialStore()));assert "codex" not in legacy.provider_ids() and legacy.fallback_provider_ids(local_only=True)==()
 registry=ConnectionRegistry(legacy);codex=registry.get("codex.default");assert codex.provider_id=="codex" and codex.auth_type.value=="cli_owned_session" and registry.models("codex.default")==[]

def test_sensitive_remote_route_requires_explicit_consent():
 router=ModelCallPolicyRouter([endpoint("remote","openai","m")],MemoryDecisionStore())
 denied=router.decide(request(privacy=Privacy.SENSITIVE,consent_granted=False));assert denied.selected_endpoint_id is None and denied.failure.value=="consent_required"
 allowed=router.decide(request(request_id="sensitive-2",privacy=Privacy.SENSITIVE,consent_granted=True));assert allowed.selected_endpoint_id=="remote"
