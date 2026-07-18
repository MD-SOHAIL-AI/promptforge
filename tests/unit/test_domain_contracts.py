from dataclasses import FrozenInstanceError
import pytest
from backend.domain_contracts import *
from backend.domain_contracts.compatibility import adapt_legacy_provider

def connection(**overrides):
 values=dict(connection_id="openrouter.primary",provider_id="openrouter",name="primary",provider_type=ProviderType.REMOTE_API,auth_type=ConnectionAuthType.API_KEY)
 values.update(overrides); return ProviderConnection(**values)
def test_independent_state_and_named_connections():
 a=connection(detected=True,authenticated=True,health=ConnectionStatus.READY,enabled=True,policy_eligible=False)
 b=connection(connection_id="openrouter.backup",name="backup")
 assert not a.policy_eligible and not a.production_eligible and a.connection_id!=b.connection_id
 with pytest.raises(FrozenInstanceError): a.enabled=False
def test_production_combination_validation():
 with pytest.raises(DomainContractError) as exc: connection(production_eligible=True)
 assert exc.value.code is FailureCode.INVALID_STATE
def test_forbidden_authority_and_capability_types():
 with pytest.raises(DomainContractError) as exc: AgentAdapterDescriptor("x.adapter","x",ProviderType.REMOTE_API,frozenset(),frozenset({"build"}))
 assert exc.value.code is FailureCode.FORBIDDEN_AUTHORITY
 with pytest.raises(DomainContractError): AgentAdapterDescriptor("x.adapter","x",ProviderType.REMOTE_API,frozenset({"apply"}))
def test_safe_serialization_redacts_nested_secrets():
 item=connection(credential_ref="vault:one",metadata={"api_key":"raw","nested":{"access_token":"raw2"},"ok":1})
 safe=item.to_safe_dict(); assert safe["credential_ref"]=="[REDACTED]" and safe["metadata"]["api_key"]=="[REDACTED]" and safe["metadata"]["nested"]["access_token"]=="[REDACTED]" and safe["metadata"]["ok"]==1
def test_schema_and_workflow_invalid_states():
 with pytest.raises(DomainContractError) as exc: connection(schema_version=2)
 assert exc.value.code is FailureCode.UNSUPPORTED_SCHEMA
 step=WorkflowStep("generate","Generate")
 with pytest.raises(DomainContractError): WorkflowRun("run","profile",1,WorkflowRunStatus.AWAITING_APPROVAL,(step,))
 with pytest.raises(DomainContractError): ApprovalRequest("approval","run","generate","apply",ApprovalStatus.APPROVED)
def test_compatibility_keeps_codex_and_openrouter_types_distinct():
 codex=adapt_legacy_provider({"provider_id":"codex_cli","provider_type":"api","enabled":False})
 router=adapt_legacy_provider({"provider_id":"openrouter","provider_type":"agent","enabled":False})
 assert codex.provider_type is ProviderType.LOCAL_CLI
 assert router.provider_type is ProviderType.REMOTE_API
 assert codex.provider_type is not router.provider_type
def test_routing_decision_state_validation():
 with pytest.raises(DomainContractError): RoutingDecision("decision","policy",RoutingDecisionStatus.SELECTED)
 assert RoutingDecision("decision","policy",RoutingDecisionStatus.REJECTED,reason_code="disabled").connection_id is None
