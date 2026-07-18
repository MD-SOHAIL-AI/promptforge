from pathlib import Path
from types import SimpleNamespace
import pytest
from backend.bridges.codex_status import CodexAlignedStatus
from backend.connection_registry import AuthState, CredentialStatus, ConnectionRegistry, ConnectionRegistryError, ConnectionRegistryFailure, LegacyProviderRegistryFacade
from backend.model_router.credentials import MemoryCredentialStore
from backend.model_router.registry import ProviderRegistry
from backend.model_router.storage import ProviderSettingsStorage

class CodexStatus:
    def __init__(self, state="unknown"): self.state=state
    def status(self): return CodexAlignedStatus(codex_installed=True,codex_version="codex-cli test",auth_status=self.state,oauth_bridge_ready=self.state=="signed_in")
class Detection:
    def __init__(self, installed=True, auth_status="unknown", confidence="low"): self.installed,self.auth_status,self.confidence=installed,auth_status,confidence
    def detect_provider(self, provider_id):
        assert provider_id=="antigravity_cli_bridge"
        return SimpleNamespace(installed=self.installed,auth_status=self.auth_status,status_confidence=self.confidence,version="agy test")
class Login:
    def __init__(self): self.calls=[]
    def launch(self, **kwargs): self.calls.append(kwargs); return SimpleNamespace()
def registry(tmp_path:Path, codex="unknown", detection=None):
    creds=MemoryCredentialStore(); storage=ProviderSettingsStorage(tmp_path/"providers.json",credential_store=creds); legacy=ProviderRegistry(storage)
    return ConnectionRegistry(legacy,codex_status_service=CodexStatus(codex),codex_login_helper=Login(),bridge_detection=detection or Detection()),creds,legacy

def test_multiple_accounts_and_api_key_lifecycle(tmp_path):
    subject,creds,_=registry(tmp_path)
    one=subject.connect("openrouter",account_name="personal",api_key="sk-personal")
    two=subject.connect("openrouter",account_name="work",api_key="sk-work")
    assert one.connection_id!=two.connection_id and creds.get("openrouter__personal")=="sk-personal" and creds.get("openrouter__work")=="sk-work"
    assert all("sk-" not in str(item.to_safe_dict()) for item in (one,two))
    subject.disconnect(one.connection_id)
    assert creds.get("openrouter__personal") is None and creds.get("openrouter__work")=="sk-work"
    removed=subject.get(one.connection_id)
    assert removed.auth_state is AuthState.AUTH_UNKNOWN and removed.credential_status is CredentialStatus.MISSING

def test_codex_signed_in_signed_out_and_unknown(tmp_path):
    for raw,expected in (("signed_in",AuthState.AUTHENTICATED),("signed_out",AuthState.SIGNED_OUT),("unknown",AuthState.AUTH_UNKNOWN)):
        subject,_,_=registry(tmp_path/raw,codex=raw)
        record=subject.refresh_status("codex.default")
        assert record.auth_state is expected
        assert subject.capabilities(record.connection_id)["agent_execution_permitted"] is False

def test_agy_unverifiable_status_is_auth_unknown(tmp_path):
    subject,_,_=registry(tmp_path,detection=Detection(auth_status="unknown",confidence="low"))
    record=subject.refresh_status("antigravity.default")
    assert record.detected and record.auth_state is AuthState.AUTH_UNKNOWN
    assert record.status_source=="official_status_unavailable"

def test_agy_only_accepts_high_confidence_official_status(tmp_path):
    subject,_,_=registry(tmp_path,detection=Detection(auth_status="authenticated",confidence="low"))
    assert subject.refresh_status("antigravity.default").auth_state is AuthState.AUTH_UNKNOWN

def test_safe_diagnostics_redacts_and_denies_auth_file_reads(tmp_path):
    subject,_,_=registry(tmp_path)
    subject.connect("openai",account_name="work",api_key="super-secret-token")
    payload=subject.safe_diagnostics()
    text=str(payload)
    assert "super-secret-token" not in text and payload["auth_files_read"] is False
    assert payload["authentication_grants_execution"] is False

def test_cli_disconnect_requires_official_provider_flow(tmp_path):
    subject,_,_=registry(tmp_path)
    with pytest.raises(ConnectionRegistryError) as exc: subject.disconnect("codex.default")
    assert exc.value.code is ConnectionRegistryFailure.DISCONNECT_UNSUPPORTED

def test_old_provider_api_compatibility(tmp_path):
    subject,creds,legacy=registry(tmp_path)
    facade=LegacyProviderRegistryFacade(legacy,connections=subject)
    configured=facade.configure_provider("openrouter",{"api_key":"legacy-key","enabled":True})
    assert configured.provider_id=="openrouter" and facade.provider("openrouter").configured
    assert creds.get("openrouter__default")=="legacy-key"
    canonical=subject.get("openrouter.default")
    assert canonical.credential_status is CredentialStatus.PRESENT and canonical.auth_state is AuthState.AUTH_UNKNOWN
    assert facade.list_models("openrouter")==legacy.list_models("openrouter")

def test_discovery_does_not_grant_execution(tmp_path):
    subject,_,_=registry(tmp_path,codex="signed_in",detection=Detection())
    records=subject.discover()
    assert next(x for x in records if x.provider_id=="codex").authenticated
    assert all(subject.capabilities(x.connection_id)["agent_execution_permitted"] is False for x in records)

