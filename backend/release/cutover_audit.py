"""Executable compatibility-removal and release-gate audit."""
from __future__ import annotations
import ast,json,re,subprocess
from dataclasses import dataclass,asdict
from pathlib import Path
from typing import Any
from backend.state.domain_persistence import DomainDatabase

@dataclass(frozen=True,slots=True)
class Gate:
 name:str;passed:bool;evidence:str;blockers:tuple[str,...]=()
@dataclass(frozen=True,slots=True)
class CutoverReport:
 releasable:bool;gates:tuple[Gate,...];callers:dict[str,tuple[str,...]]
 def to_dict(self):return {"releasable":self.releasable,"gates":[asdict(x) for x in self.gates],"callers":{k:list(v) for k,v in self.callers.items()}}

PATTERNS={
 "duplicate_registries":("ProductProviderRegistry","CodingProviderRegistry","BridgeProviderRegistry"),
 "jsonl_workflow_authority":("CodingWorkflowStore.from_state_directory","coding_workflow_store(request)"),
 "legacy_agent_panels":("ProductAgentPanel","UnifiedCodingWorkflowPanel","NEXT_PUBLIC_FORGEX_AGENT_LEGACY_COMPAT"),
 "browser_conversation_authority":("localStorage.setItem(\"forgex.agent","localStorage.setItem(\"forgex.coding"),
 "codex_model_routing":("CodexAgentProvider","providers.codex_agent"),
 "compatibility_facades":("LegacyProviderRegistryFacade","CodingWorkflowCompatibilityFacade"),
 "legacy_feature_flags":("FORGEX_ENABLE_UNIFIED_CODING_WORKFLOW","FORGEX_ENABLE_AGENT_RUNTIME_FAKE_PROVIDER","NEXT_PUBLIC_FORGEX_AGENT_LEGACY_COMPAT"),
}
EXCLUDED_PARTS={".git","node_modules",".next","__pycache__","tests","docs","promptforge-promo-video"}
def inventory(root:Path)->dict[str,tuple[str,...]]:
 result={key:[] for key in PATTERNS}
 try:
  listed=subprocess.run(["git","ls-files","-co","--exclude-standard"],cwd=root,capture_output=True,text=True,check=True,timeout=20).stdout.splitlines()
  candidates=(root/item for item in listed)
 except Exception:candidates=root.rglob("*")
 for path in candidates:
  if not path.is_file() or path.suffix not in {".py",".ts",".tsx",".js",".mjs"} or any(part in EXCLUDED_PARTS for part in path.parts):continue
  try:text=path.read_text(encoding="utf-8")
  except (OSError,UnicodeError):continue
  relative=path.relative_to(root).as_posix()
  if relative.startswith(("backend/release/","backend/migrations/")):continue
  for category,patterns in PATTERNS.items():
   if any(pattern in text for pattern in patterns):result[category].append(relative)
 return {key:tuple(sorted(set(value))) for key,value in result.items()}
def _read(root:Path,relative:str)->str:
 try:return (root/relative).read_text(encoding="utf-8")
 except (OSError,UnicodeError):return ""
def _artifact_persistence_fail_closed(root:Path)->tuple[bool,str,tuple[str,...]]:
 """Targeted AST gate for authoritative artifact/state/event atomicity."""
 blockers=[]
 try:
  engine_tree=ast.parse(_read(root,"backend/agent_runtime/workflow_engine.py"))
  state_tree=ast.parse(_read(root,"backend/state/durable_workflow.py"))
 except SyntaxError:
  return False,"Authoritative artifact sources could not be parsed.",( "artifact_source_syntax_error",)
 def class_node(tree,name):
  return next((node for node in tree.body if isinstance(node,ast.ClassDef) and node.name==name),None)
 def method(node,name):
  if node is None:return None
  return next((item for item in node.body if isinstance(item,(ast.FunctionDef,ast.AsyncFunctionDef)) and item.name==name),None)
 def call_name(call):
  return call.func.attr if isinstance(call.func,ast.Attribute) else call.func.id if isinstance(call.func,ast.Name) else ""
 def broad(handler):
  if handler.type is None:return True
  names=[]
  for node in ast.walk(handler.type):
   if isinstance(node,ast.Name):names.append(node.id)
  return any(name in {"Exception","BaseException"} for name in names)
 def handler_raises(handler):
  return any(isinstance(node,ast.Raise) for statement in handler.body for node in ast.walk(statement))
 engine=class_node(engine_tree,"AgentWorkflowEngine");state=class_node(state_tree,"DurableWorkflowStateMachine")
 if engine is None:blockers.append("workflow_engine_class_missing")
 if state is None:blockers.append("durable_state_machine_class_missing")
 authoritative=[]
 if engine is not None:
  for item in engine.body:
   if isinstance(item,(ast.FunctionDef,ast.AsyncFunctionDef)) and ("artifact" in item.name or any(isinstance(node,ast.Call) and call_name(node)=="_move_with_artifact" for node in ast.walk(item))):authoritative.append(item)
 if state is not None:
  authoritative.extend(item for item in state.body if isinstance(item,(ast.FunctionDef,ast.AsyncFunctionDef)) and item.name in {"transition_with_artifact","_persist_authoritative_artifact"})
 for item in authoritative:
  if any(isinstance(node,ast.ExceptHandler) and broad(node) and not handler_raises(node) for node in ast.walk(item)):blockers.append(f"broad_artifact_exception_suppressed:{item.name}")
 engine_text=_read(root,"backend/agent_runtime/workflow_engine.py")
 if "_record_artifact" in engine_text or "repo.insert(\"artifacts\"" in engine_text:blockers.append("separate_or_best_effort_artifact_insert")
 kinds=set();atomic_targets=set();unsafe_targets=set()
 if engine is not None:
  for node in ast.walk(engine):
   if not isinstance(node,ast.Call):continue
   name=call_name(node)
   target=node.args[1].attr if len(node.args)>1 and isinstance(node.args[1],ast.Attribute) else None
   if name=="_move_with_artifact":
    atomic_targets.add(target)
    for keyword in node.keywords:
     if keyword.arg=="kind" and isinstance(keyword.value,ast.Constant):kinds.add(keyword.value.value)
   elif name=="_move" and target in {"AWAITING_REVIEW","AWAITING_BUILD","AWAITING_FLASH_APPROVAL"}:unsafe_targets.add(target)
 if not {"review","applied","build"}.issubset(kinds):blockers.append("authoritative_artifact_kind_not_atomic")
 if not {"AWAITING_REVIEW","AWAITING_BUILD","AWAITING_FLASH_APPROVAL"}.issubset(atomic_targets):blockers.append("artifact_success_state_not_atomic")
 if unsafe_targets:blockers.append("success_state_uses_non_artifact_transition")
 transition=method(state,"transition");transition_with=method(state,"transition_with_artifact");persist=method(state,"_persist_authoritative_artifact")
 transaction_scope=None
 if transition is not None:
  transaction_scope=next((node for node in ast.walk(transition) if isinstance(node,ast.With) and any(any(isinstance(inner,ast.Call) and call_name(inner)=="transaction" for inner in ast.walk(item.context_expr)) for item in node.items)),None)
 constants=" ".join(str(node.value) for node in ast.walk(transaction_scope) if isinstance(node,ast.Constant)) if transaction_scope is not None else ""
 calls={call_name(node) for node in ast.walk(transaction_scope) if isinstance(node,ast.Call)} if transaction_scope is not None else set()
 if transaction_scope is None or "_persist_authoritative_artifact" not in calls:blockers.append("artifact_not_in_transition_transaction")
 for record in ("workflow_runs","workflow_events","workflow_event_outbox","conversation_messages","workflow_commands"):
  if record not in constants:blockers.append(f"atomic_record_missing:{record}")
 if transition_with is None or not any(isinstance(node,ast.Call) and call_name(node)=="transition" for node in ast.walk(transition_with)):blockers.append("atomic_transition_entrypoint_missing")
 persist_constants=" ".join(str(node.value) for node in ast.walk(persist) if isinstance(node,ast.Constant)) if persist is not None else ""
 if "INSERT INTO artifacts" not in persist_constants:blockers.append("durable_artifact_insert_missing")
 if "ARTIFACT_PERSISTENCE_FAILED" not in _read(root,"backend/state/durable_workflow.py"):blockers.append("safe_artifact_error_code_missing")
 passed=not blockers
 evidence="Artifact, hash metadata, state, event, outbox, projection, and idempotency command share one fail-closed SQLite transaction." if passed else "Authoritative artifact persistence can progress without a proven durable atomic commit."
 return passed,evidence,tuple(blockers)
def audit(root:Path,database:DomainDatabase)->CutoverReport:
 callers=inventory(root);gates=[]
 model_source=_read(root,"backend/model_router/models.py")
 registry_source=_read(root,"backend/model_router/registry.py")
 router_source=_read(root,"backend/model_router/router.py")
 policy_router_source=_read(root,"backend/model_router/policy_router.py")
 code_generation_source=_read(root,"backend/services/code_generation_service.py")
 fallback_contained=(
  "allow_fallback: bool = False" in model_source
  and "fallback_enabled: bool = False" in model_source
  and "fallback_enabled=bool(saved.get(\"fallback_enabled\", False))" in registry_source
  and "allow_fallback=allow_fallback if isinstance(allow_fallback, bool) else False" in router_source
  and "FALLBACK_NOT_AUTHORIZED" in router_source
  and "provider_group_membership" in router_source
  and "NON_FALLBACK_ERRORS" in policy_router_source
  and "request.consent_granted and selected.provider_group is not None" in policy_router_source
  and "c.provider_group==selected.provider_group" in policy_router_source
  and "fallback_enabled: bool = False" in code_generation_source
  and '"allow_fallback": not fallback_used' not in code_generation_source
  and '"allow_fallback": _metadata_bool(request.context.metadata, "fallback_enabled", False) and not fallback_used' in code_generation_source
  and "_builtin_fallback_allowed" in code_generation_source
 )
 gates.append(Gate("phase1_fallback_default_deny",fallback_contained,"Legacy model fallback must default off and require verified policy/consent evidence.",(() if fallback_contained else ("fallback_default_or_authorization_guard_missing",))))
 store_source=_read(root,"backend/agent_runtime/coding_workflow_store.py")
 app_source=_read(root,"backend/api/app.py")
 workflow_route_source=_read(root,"backend/api/routes/coding_workflow.py")
 jsonl_contained=("allow_new_runs=False, allow_mutations=False" in app_source and "LEGACY_WORKFLOW_ADMISSION_DISABLED" in store_source and "LEGACY_WORKFLOW_READ_ONLY" in store_source and "if not self._allow_new_runs" in store_source and "if not self._allow_mutations" in store_source and "_deny_legacy_workflow_admission(request)" in workflow_route_source and "_deny_legacy_workflow_mutation(request)" in workflow_route_source)
 gates.append(Gate("phase1_jsonl_admission_contained",jsonl_contained,"Production JSONL stores must be read-compatible but reject every new workflow.",(() if jsonl_contained else ("jsonl_admission_guard_missing",))))
 codex_adapter_source=_read(root,"backend/agent_runtime/codex_cli_adapter.py")
 agy_adapter_source=_read(root,"backend/agent_runtime/agy_cli_adapter.py")
 cli_contained=("Production eligibility remains disabled" in codex_adapter_source and "\"production_eligible\":False" in codex_adapter_source and "Production eligibility remains disabled" in agy_adapter_source and "\"production_eligible\":False" in agy_adapter_source and all(value in agy_adapter_source for value in ('"apply":False','"build":False','"flash":False','"monitor":False')))
 gates.append(Gate("phase1_cli_adapters_contained",cli_contained,"Codex and AGY must remain review-only and production-ineligible.",(() if cli_contained else ("cli_adapter_containment_missing",))))
 unified_source=_read(root,"backend/api/routes/agent_workspace.py")
 unified_contained=("production_eligible=False" in unified_source and "AGENT_EXECUTION_PARITY_REQUIRED" in unified_source and "CodingWorkflowCompatibilityFacade" not in unified_source)
 gates.append(Gate("phase1_unified_execution_fail_closed",unified_contained,"Incomplete unified execution must fail closed without legacy routing.",(() if unified_contained else ("unified_execution_containment_missing",))))
 gates.append(Gate("codex_out_of_model_routing",not callers["codex_model_routing"],"Codex must exist only as cli_owned_session Connection and AgentAdapter.",callers["codex_model_routing"]))
 gates.append(Gate("single_registry_contract",not callers["duplicate_registries"],"Production callers must use Connection Registry plus AgentAdapter Registry.",callers["duplicate_registries"]))
 gates.append(Gate("sqlite_workflow_authority",not callers["jsonl_workflow_authority"],"Durable SQLite workflows must be the only production authority.",callers["jsonl_workflow_authority"]))
 gates.append(Gate("backend_conversation_authority",not callers["browser_conversation_authority"],"Browser storage cannot own conversation state.",callers["browser_conversation_authority"]))
 gates.append(Gate("unified_agent_ui",not callers["legacy_agent_panels"],"Legacy panels and their compatibility flag must be absent.",callers["legacy_agent_panels"]))
 gates.append(Gate("redundant_feature_flags_removed",not callers["legacy_feature_flags"],"Cutover-only feature flags must have no production callers.",callers["legacy_feature_flags"]))
 adapter_source=_read(root,"backend/agent_runtime/agent_adapter.py")
 authority_guard=all(value in adapter_source for value in ("active_workspace_mutation","apply","build","flash","monitor","approve","secret_access"))
 gates.append(Gate("provider_authority_denied",authority_guard,"AgentAdapter descriptors must reject every ForgeX-owned authority.",(() if authority_guard else ("descriptor_guard_missing",))))
 engine_source=_read(root,"backend/agent_runtime/workflow_engine.py")
 artifact_closed,artifact_evidence,artifact_blockers=_artifact_persistence_fail_closed(root)
 gates.append(Gate("artifact_persistence_fail_closed",artifact_closed,artifact_evidence,artifact_blockers))
 hardware_guard="_consume_approval(run_id,\"flash\"" in engine_source and "_require_build_artifact" in engine_source and "_consume_approval(run_id,\"apply\"" in engine_source
 gates.append(Gate("hardware_mutation_requires_forgex_approval",hardware_guard,"Apply and flash must consume bound ForgeX approvals before executor leases.",(() if hardware_guard else ("approval_guard_missing",))))
 with database.connect() as db:
  unbound=db.execute("SELECT COUNT(*) FROM workflow_runs r LEFT JOIN workflow_run_profile_bindings b ON b.run_id=r.run_id WHERE b.run_id IS NULL").fetchone()[0]
  unsafe_approvals=db.execute("SELECT COUNT(*) FROM approvals WHERE action_code IN('flash','apply') AND (json_extract(payload_json,'$.binding_hash') IS NULL OR json_extract(payload_json,'$.expires_at') IS NULL)").fetchone()[0]
  undecided_usage=db.execute("SELECT COUNT(*) FROM usage_records u WHERE u.run_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM routing_decisions d WHERE json_extract(d.payload_json,'$.run_id')=u.run_id OR json_extract(d.payload_json,'$.request_id')=u.idempotency_key)").fetchone()[0]
 gates.append(Gate("profile_versioned_runs",unbound==0,f"{unbound} durable run(s) lack exact immutable profile bindings.",(() if unbound==0 else (str(unbound),))))
 gates.append(Gate("hardware_and_apply_approval_binding",unsafe_approvals==0,f"{unsafe_approvals} mutation approval(s) lack binding and expiry.",(() if unsafe_approvals==0 else (str(unsafe_approvals),))))
 router_persists_decisions="RoutingDecisionStore" in router_source or "self.decision_store" in router_source
 explainable=undecided_usage==0 and router_persists_decisions
 blockers=tuple(([str(undecided_usage)] if undecided_usage else [])+([] if router_persists_decisions else ["router_does_not_persist_decision_per_call"]))
 gates.append(Gate("explainable_model_calls",explainable,f"{undecided_usage} persisted usage record(s) lack decisions; router persistence={router_persists_decisions}.",blockers))
 fail_closed=_read(root,"backend/api/routes/agent_workspace.py")
 parity="Executor parity is not production eligible" not in fail_closed
 gates.append(Gate("agent_runtime_parity",parity,"Unified Agent Workspace must complete proposal/apply/build/flash/monitor parity before legacy removal.",(() if parity else ("fail_closed_executor",))))
 return CutoverReport(all(x.passed for x in gates),tuple(gates),callers)
def write_report(report:CutoverReport,path:Path):
 path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(report.to_dict(),indent=2,sort_keys=True)+"\n",encoding="utf-8")


def main():
 import argparse
 parser=argparse.ArgumentParser(description="Audit ForgeX compatibility-removal release gates.")
 parser.add_argument("--root",type=Path,default=Path.cwd());parser.add_argument("--database",type=Path,required=True);parser.add_argument("--output",type=Path)
 args=parser.parse_args();report=audit(args.root.resolve(),DomainDatabase(args.database))
 if args.output:write_report(report,args.output)
 print(json.dumps(report.to_dict(),indent=2,sort_keys=True))
 raise SystemExit(0 if report.releasable else 1)
if __name__=="__main__":main()

