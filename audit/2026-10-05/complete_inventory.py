"""Correct root-only data exclusions and separately inventory configuration/fixtures."""
import ast
import collections
import hashlib
import json
import os
import re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
OUT=Path(__file__).parent
data=json.loads((OUT/'inventory.json').read_text())
known={r['path'] for r in data['files']}
hashes=json.loads((OUT/'source-hashes-before.json').read_text())
searches=json.loads((OUT/'global-searches.json').read_text())
excluded={'node_modules','.git','.venv','__pycache__','dist','build','audit','.pytest_cache','test-results','playwright-report'}
configs=[]
added=[]
for directory,dirs,names in os.walk(ROOT):
    here=Path(directory)
    dirs[:]=[name for name in dirs if name not in excluded and not name.startswith(('.next','.forgex','.promptforge')) and not (here==ROOT and name in {'workspace','assets'}) and not (here==ROOT/'frontend' and name=='public')]
    for name in names:
        p=here/name
        rel=p.relative_to(ROOT).as_posix()
        if p.suffix in {'.py','.c','.cc','.cpp','.h','.hpp','.ino'} and rel not in known:
            raw=p.read_bytes(); text=raw.decode('utf-8-sig',errors='replace')
            r={'path':rel,'loc':len(text.splitlines()),'sha256':hashlib.sha256(raw).hexdigest(),'kind':'test' if rel.startswith('tests/') else 'owned','symbols':[],'imports':[],'calls':[],'routes':[]}
            if p.suffix=='.py':
                tree=ast.parse(text)
                r['purpose']=ast.get_docstring(tree) or ''
                for n in ast.walk(tree):
                    if isinstance(n,(ast.Import,ast.ImportFrom)):r['imports'].append(ast.unparse(n))
                    if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)):r['symbols'].append({'name':n.name,'line':n.lineno,'end':n.end_lineno,'size':n.end_lineno-n.lineno+1,'type':type(n).__name__,'purpose':ast.get_docstring(n) or ''})
                    if isinstance(n,ast.Call):r['calls'].append({'name':ast.unparse(n.func),'line':n.lineno})
            data['files'].append(r);hashes[rel]=r['sha256'];added.append(rel)
            for term in searches:
                matches=[{'line':i,'text':line[:300]} for i,line in enumerate(text.splitlines(),1) if re.search(re.escape(term),line,re.I)]
                if matches:searches[term].append({'path':rel,'matches':matches})
        elif p.suffix in {'.json','.toml','.yaml','.yml'} or name.startswith('requirements') or name in {'.env.example','SKILL.md'}:
            configs.append({'path':rel,'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'kind':'fixture' if rel.startswith('tests/') else 'vendor' if rel.startswith(('third_party/','frontend/vendor/')) else 'configuration'})
data['files'].sort(key=lambda r:r['path'])
records=data['files']
data['summary']={'files':len(records),'loc':sum(r['loc'] for r in records),'by_extension':dict(collections.Counter(Path(r['path']).suffix for r in records)),'by_kind':dict(collections.Counter(r['kind'] for r in records)),
    'by_top':{top:{'files':len(g),'loc':sum(r['loc'] for r in g)} for top in sorted({r['path'].split('/')[0] for r in records}) for g in [[r for r in records if r['path'].split('/')[0]==top]]},'syntax_errors':[]}
for filename,value in [('inventory.json',data),('source-hashes-before.json',hashes),('global-searches.json',searches),('configuration-inventory.json',configs)]:
    (OUT/filename).write_text(json.dumps(value,indent=2),encoding='utf-8')
print(json.dumps({'added':added,'summary':data['summary'],'config_files':len(configs)},indent=2))
