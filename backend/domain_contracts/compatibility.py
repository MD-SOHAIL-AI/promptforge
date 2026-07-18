"""Read-only compatibility mappings for existing registries."""
from typing import Any, Mapping
from .v1 import AgentAdapterDescriptor,AgentCapability,ConnectionAuthType,ConnectionStatus,ProviderConnection,ProviderType
_MAP={"generate_files":AgentCapability.GENERATE_FILES,"modify_files":AgentCapability.MODIFY_FILES,"suggest_commands":AgentCapability.SUGGEST_COMMANDS,"read_selected_context":AgentCapability.READ_SELECTED_CONTEXT,"run_in_cli_sandbox":AgentCapability.RUN_IN_SANDBOX,"create_patch":AgentCapability.CREATE_PATCH,"create_review":AgentCapability.CREATE_REVIEW}
def _get(x,n,d=None): return x.get(n,d) if isinstance(x,Mapping) else getattr(x,n,d)
def _val(x): return getattr(x,"value",x)
def _type(pid,legacy=None,kind=None):
 if pid in {"codex","codex_cli","codex_bridge","codex_cli_oauth_bridge","codex_cli_subscription"}: return ProviderType.LOCAL_CLI
 if pid=="openrouter": return ProviderType.REMOTE_API
 text=str(_val(legacy) or _val(kind) or "").lower()
 if "cli" in text:return ProviderType.LOCAL_CLI
 if "server" in text:return ProviderType.LOCAL_SERVER
 if "template" in text:return ProviderType.VERIFIED_TEMPLATE
 if "api" in text:return ProviderType.REMOTE_API
 return ProviderType.MANUAL
def adapt_legacy_provider(item:object,*,connection_name="default"):
 pid=str(_get(item,"provider_id","")); pt=_type(pid,_get(item,"provider_type"),_get(item,"kind")); enabled=bool(_get(item,"enabled",_get(item,"execution_allowed",False))); detected=bool(_get(item,"detected",enabled)); auth=str(_val(_get(item,"auth_status",""))).lower(); authenticated=bool(_get(item,"authenticated",False)) or auth in {"passed","authenticated","signed_in","ready"}
 if pt is ProviderType.VERIFIED_TEMPLATE: at,authenticated=ConnectionAuthType.LOCAL_NO_AUTH,True
 elif pt is ProviderType.LOCAL_CLI: at=ConnectionAuthType.CLI_OWNED_SESSION
 else: at=ConnectionAuthType.API_KEY
 ready=str(_val(_get(item,"readiness",_get(item,"status","")))).lower() in {"ready","connected","active"}; prod=bool(_get(item,"production_eligible",False)); policy=bool(_get(item,"routing_allowed",_get(item,"review_eligible",prod)))
 if prod: detected=authenticated=enabled=policy=ready=True
 return ProviderConnection(f"{pid}.{connection_name}",pid,connection_name,pt,at,detected,authenticated,ConnectionStatus.READY if ready else ConnectionStatus.UNKNOWN,enabled,policy,prod)
def adapt_coding_provider_descriptor(item:object,*,connection_name="default"):
 conn=adapt_legacy_provider(item,connection_name=connection_name); caps=frozenset(_MAP[str(_val(x))] for x in _get(item,"capabilities",()) if str(_val(x)) in _MAP); return conn,AgentAdapterDescriptor(f"{conn.provider_id}.legacy",conn.provider_id,conn.provider_type,caps)
def adapt_product_provider_entry(item:object,*,connection_name="default"): return adapt_legacy_provider(item,connection_name=connection_name)
