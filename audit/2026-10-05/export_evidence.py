"""Export structural evidence, static cross references, schemas and source integrity."""
import ast
import collections
import dataclasses
import hashlib
import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
inventory = json.loads((OUT/'inventory.json').read_text(encoding='utf-8'))
files = inventory['files']
ts = {r['path']:r for r in json.loads((OUT/'typescript-analysis.json').read_text())}
by_path = {r['path']:r for r in files}
modules = {r['path'][:-3].replace('/','.'):(r['path']) for r in files if r['path'].endswith('.py')}
modules.update({k.removesuffix('.__init__'):v for k,v in list(modules.items()) if k.endswith('.__init__')})
edges = collections.defaultdict(set)
routes = []
duplicate_functions = []
broad_handlers = []
functions = []
for r in files:
    path = r['path']
    text = (ROOT/path).read_text(encoding='utf-8-sig', errors='replace')
    if path.endswith('.py'):
        tree = ast.parse(text)
        root_module = path[:-3].replace('/','.')
        package = root_module.rsplit('.',1)[0] if not path.endswith('__init__.py') else root_module.removesuffix('.__init__')
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                base = '.'.join(package.split('.')[:len(package.split('.'))-node.level+1]) if node.level else ''
                prefix = '.'.join(x for x in (base,node.module) if x)
                names = [prefix]+[prefix+'.'+a.name for a in node.names]
            else:
                names=[]
            for name in names:
                if name in modules: edges[modules[name]].add(path)
            if isinstance(node,ast.ExceptHandler) and (node.type is None or ast.unparse(node.type) in {'Exception','BaseException'}):
                broad_handlers.append({'path':path,'line':node.lineno,'end':node.end_lineno,'pass_only':all(isinstance(x,ast.Pass) for x in node.body)})
        names = collections.Counter(n.name for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)))
        duplicate_functions.extend({'path':path,'name':name,'count':count} for name,count in names.items() if count>1)
        functions.extend({**s,'path':path,'kind':r['kind']} for s in r['symbols'] if s['type'] in {'FunctionDef','AsyncFunctionDef'})
        prefixes = re.findall(r'APIRouter\([^\n]*prefix\s*=\s*["\']([^"\']+)',text)
        for route in r['routes']: routes.append({**route,'file':path,'router_prefix':prefixes[0] if prefixes else ''})
    elif path in ts:
        functions.extend({**s,'path':path,'kind':r['kind'],'type':'TSFunction'} for s in ts[path]['functions'])
        for imp in ts[path]['imports']:
            if imp.startswith('@/'):
                base=Path('frontend')/imp[2:]
            elif imp.startswith('.'):
                base=Path(path).parent/imp
            else:
                continue
            base=Path(__import__('os').path.normpath(base)).as_posix()
            for candidate in [base,*[base+ext for ext in ('.ts','.tsx','.mjs','.mts','.js')],*[base+'/index'+ext for ext in ('.ts','.tsx','.js')]]:
                if candidate in by_path:
                    edges[candidate].add(path)
                    break

from backend.model_router.registry import PROVIDERS
from backend.model_router.opencode_catalog import provider_data
providers = [{**dataclasses.asdict(v),'catalog_sdk':provider_data(k).get('npm'),'catalog_models':len(provider_data(k).get('models',{})),
              'catalog_env':provider_data(k).get('env',[])} for k,v in PROVIDERS.items()]
from backend.agent_runtime.tool_registry import describe_tools
from backend.agent_runtime.capability_profiles import ASK_PROFILE,PLAN_PROFILE,EDIT_PROFILE,BUILD_ONLY_PROFILE,BUILD_REPAIR_PROFILE,SIMULATE_PROFILE
from backend.agent_runtime.tool_contracts import ToolName
from backend.agent_runtime.tool_registry import HOST_TOOLS
tool_schema=describe_tools(BUILD_REPAIR_PROFILE.policy(),handlers=HOST_TOOLS,native=ToolName)
for tool in tool_schema:
    tool['profiles']=[p.profile_id for p in (ASK_PROFILE,PLAN_PROFILE,EDIT_PROFILE,BUILD_ONLY_PROFILE,BUILD_REPAIR_PROFILE,SIMULATE_PROFILE) if ToolName(tool['name']) in p.allowed_tools]
    tool.pop('available',None)  # Schema inventory must not assert every supplied dummy handler is wired.

group_counts={}
for filename in ('python-unit.xml','python-api-integration.xml'):
    doc=ET.parse(OUT/filename)
    counts=collections.defaultdict(collections.Counter)
    for case in doc.iter('testcase'):
        cls=case.attrib.get('classname','')
        group=cls.split('.')[1] if cls.startswith('tests.') else cls.split('.')[0]
        status='failed' if case.find('failure') is not None or case.find('error') is not None else 'skipped' if case.find('skipped') is not None else 'passed'
        counts[group][status]+=1
    group_counts[filename]={g:dict(c) for g,c in counts.items()}
before=json.loads((OUT/'source-hashes-before.json').read_text())
changed=[]
for path,digest in before.items():
    p=ROOT/path
    current=hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None
    if current!=digest:changed.append({'path':path,'before':digest,'after':current})
owned=[r for r in files if r['kind']=='owned']
owned_functions=[f for f in functions if f['kind']=='owned']
evidence={'stats':inventory['summary'],'owned_files_over_500':sum(r['loc']>500 for r in owned),'owned_files_over_1000':sum(r['loc']>1000 for r in owned),
          'owned_functions_over_100':sum(f['size']>100 for f in owned_functions),'owned_functions_over_150':sum(f['size']>150 for f in owned_functions),
          'largest_functions':sorted(owned_functions,key=lambda f:f['size'],reverse=True)[:60],
          'duplicate_top_level_functions':duplicate_functions,'broad_exception_handlers':broad_handlers,
          'test_groups':group_counts,'changed_source_hashes':changed,'provider_count':len(providers),'tool_count':len(tool_schema)}
for name,value in [('evidence-summary.json',evidence),('static-import-callers.json',{k:sorted(v) for k,v in edges.items()}),('api-routes.json',routes),('providers.json',providers),('tool-schemas.json',tool_schema)]:
    (OUT/name).write_text(json.dumps(value,indent=2),encoding='utf-8')
(OUT/'git-status-after.txt').write_text(subprocess.run(['git','status','--short'],cwd=ROOT,capture_output=True,text=True).stdout,encoding='utf-8')
print(json.dumps({k:v for k,v in evidence.items() if k not in {'largest_functions','broad_exception_handlers','stats'}},indent=2))
