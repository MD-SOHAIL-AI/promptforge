from types import SimpleNamespace
import pytest
from backend.api.errors import APIError
from backend.api.routes.control_plane import _command,_view
from backend.state.domain_persistence import DomainDatabase

class Record:
 def to_safe_dict(self):
  return {"connection_id":"openrouter.default","provider_id":"openrouter","account_name":"default","display_name":"OpenRouter","provider_type":"remote_api","auth_type":"api_key","auth_state":"authenticated","transport_status":"ready","detected":True,"authenticated":True,"enabled":True}

def request_for(database):
 return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(workflow_database=database)))

def test_connection_view_keeps_all_eligibility_axes_independent():
 view=_view(Record())
 assert view["connected"] is True
 assert view["authenticated"] is True
 assert view["enabled"] is True
 assert view["healthy"] is True
 assert view["policy_eligible"] is False
 assert view["routing_eligibility"]=="not_evaluated"
 assert view["production_eligible"] is False

def test_control_plane_commands_are_durable_and_idempotent(tmp_path):
 database=DomainDatabase(tmp_path/"state.db");database.initialize();request=request_for(database);calls=0
 def execute():
  nonlocal calls;calls+=1;return {"ok":True}
 first=_command(request,"test.command","command-0001",{"value":1},execute)
 second=_command(request,"test.command","command-0001",{"value":1},execute)
 assert first==second=={"ok":True}
 assert calls==1
 with database.connect() as db:
  assert db.execute("SELECT COUNT(*) FROM control_plane_commands").fetchone()[0]==1

def test_idempotency_key_reuse_with_other_payload_fails_closed(tmp_path):
 database=DomainDatabase(tmp_path/"state.db");database.initialize();request=request_for(database)
 _command(request,"test.command","command-0002",{"value":1},lambda:{"ok":True})
 with pytest.raises(APIError) as error:
  _command(request,"test.command","command-0002",{"value":2},lambda:{"ok":False})
 assert error.value.status_code==409

def test_every_command_requires_an_idempotency_key(tmp_path):
 database=DomainDatabase(tmp_path/"state.db");database.initialize()
 with pytest.raises(APIError) as error:
  _command(request_for(database),"test.command",None,{},lambda:{"ok":True})
 assert error.value.status_code==422
