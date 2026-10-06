"""Audit-only reproductions; never touch an existing project or device."""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backend.agent_runtime.skills import SkillRegistry
from backend.agent_runtime.workspace_index import WorkspaceIndex
from backend.changes.service import ChangeSetService, ChangeSetError
import backend.changes.service as changes

OUT = Path(__file__).parent
FIX = OUT / 'probe-fixtures'

def crash_child(base: Path, identifier: str):
    service = ChangeSetService(state_root=base / 'state', staging_root=base / 'stages')
    original = changes._atomic_write_bytes
    active = (base / 'active').resolve()
    def exit_after_write(path, data):
        original(path, data)
        if path.parent == active:
            os._exit(73)
    changes._atomic_write_bytes = exit_after_write
    service.apply(identifier)

if len(sys.argv) > 1 and sys.argv[1] == 'crash-child':
    crash_child(Path(sys.argv[2]), sys.argv[3])
    raise SystemExit

results = {}
workspace = FIX / 'skill-workspace'
registry = SkillRegistry(workspace)
results['skill_junction'] = {
    'python': sys.version.split()[0],
    'isjunction_available': hasattr(os.path, 'isjunction'),
    'path_is_symlink': (workspace / '.agents' / 'skills').is_symlink(),
    'resolved_root': str((workspace / '.agents' / 'skills').resolve()),
    'discovered': [item.name for item in registry.discover()],
    'loaded': registry.load('external-probe'),
}
index = WorkspaceIndex(FIX / 'index-workspace')
results['index_junction'] = {
    'files': [item.path for item in index.files()],
    'external_candidate': [item.path for item in index.query('outside_audit_marker')],
}

base = FIX / 'apply-crash'
active = base / 'active'
active.mkdir(parents=True, exist_ok=False)
for name in ('a.txt', 'b.txt'):
    (active / name).write_text('before\n', encoding='utf-8')
service = ChangeSetService(state_root=base / 'state', staging_root=base / 'stages')
baseline = service.inspect_workspace(active)
stage = service.create_stage()
for name in ('a.txt', 'b.txt'):
    (stage / name).write_text('after\n', encoding='utf-8')
change_set = service.create_from_stage(provider_id='audit-fake', workspace_root=active,
    stage_root=stage, baseline=baseline, authorized_paths=('a.txt', 'b.txt'))
child = subprocess.run([sys.executable, str(Path(__file__).resolve()), 'crash-child',
    str(base.resolve()), change_set.change_set_id], cwd=ROOT, check=False)
reloaded = ChangeSetService(state_root=base / 'state', staging_root=base / 'stages')
crash = {'exit_code': child.returncode, 'persisted_status': reloaded.get(change_set.change_set_id).status,
    'files': {p.name: p.read_text() for p in active.glob('*.txt')}}
for action in ('undo', 'apply'):
    try:
        getattr(reloaded, action)(change_set.change_set_id)
        crash[action] = 'unexpected success'
    except ChangeSetError as exc:
        crash[action] = exc.code
results['changeset_crash'] = crash
(OUT / 'safety-probes.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
print(json.dumps(results, indent=2))
