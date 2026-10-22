"""Validate audit artifact coverage/links and final source integrity, read-only product."""
import hashlib
import json
import re
import subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
OUT=Path(__file__).parent
inventory=json.loads((OUT/'inventory.json').read_text())
terms=['codex_oauth','CODEX_OAUTH','Google OAuth','Gemini OAuth','Google AI subscription','AI One','AI Pro','subscription authentication',
       'Model Context Protocol','MCP server','MCP client','stdio transport','Streamable HTTP','tools/list','resources/list','prompts/list']
focus={term:[] for term in terms}
for record in inventory['files']:
    p=ROOT/record['path']; lines=p.read_text(encoding='utf-8-sig',errors='replace').splitlines()
    for term in terms:
        matches=[{'line':i,'text':text[:250]} for i,text in enumerate(lines,1) if term.casefold() in text.casefold()]
        if matches:focus[term].append({'path':record['path'],'kind':record['kind'],'matches':matches})
for p in [ROOT/'README.md',ROOT/'.env.example',*(ROOT/'docs').rglob('*.md')]:
    for term in terms:
        matches=[{'line':i,'text':text[:250]} for i,text in enumerate(p.read_text(encoding='utf-8-sig',errors='replace').splitlines(),1) if term.casefold() in text.casefold()]
        if matches:focus[term].append({'path':p.relative_to(ROOT).as_posix(),'kind':'documentation/configuration','matches':matches})
(OUT/'focused-searches.json').write_text(json.dumps(focus,indent=2),encoding='utf-8')
report=(OUT/'ForgeX-Complete-Code-Audit.md').read_text(encoding='utf-8')
sections=[int(x) for x in re.findall(r'^## (\d+)\.',report,re.M)]
bad_links=[]
for p in [OUT/'ForgeX-Complete-Code-Audit.md',OUT/'File-by-File-Audit.md',OUT/'Provider-and-API-Inventories.md']:
    for target in re.findall(r'\]\(([^)]+)\)',p.read_text(encoding='utf-8')):
        if target.startswith(('https://','http://','#')):continue
        plain=re.sub(r':\d+$','',target)
        candidate=Path(plain) if re.match(r'^[A-Za-z]:/',plain) else p.parent/plain
        if not candidate.exists():bad_links.append({'document':p.name,'target':target})
before=json.loads((OUT/'source-hashes-before.json').read_text())
changes=[]
for name,expected in before.items():
    p=ROOT/name;actual=hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None
    if actual!=expected:changes.append({'path':name,'expected':expected,'actual':actual})
coverage=json.loads((OUT/'coverage-ledger.json').read_text())
configuration_changes=[]
for record in json.loads((OUT/'configuration-inventory.json').read_text()):
    p=ROOT/record['path']
    if hashlib.sha256(p.read_bytes()).hexdigest()!=record['sha256']:
        configuration_changes.append(record['path'])
final_status=subprocess.run(['git','status','--short'],cwd=ROOT,capture_output=True,text=True).stdout
(OUT/'git-status-final.txt').write_text(final_status,encoding='utf-8')
testsummary=json.loads((OUT/'evidence-summary.json').read_text())['test_groups']
result={'sections':sections,'all_39_sections':sections==list(range(1,40)),
        'source_files':len(inventory['files']),'coverage_total':sum(map(len,coverage.values())),
        'source_hash_changes':changes,'configuration_hash_changes':configuration_changes,'broken_local_links':bad_links,'python_outcomes':testsummary,
        'dependency_audit':{name:json.loads((OUT/name).read_text(encoding='utf-8-sig')).get('metadata',{}).get('vulnerabilities') for name in ['npm-audit-root.json','npm-audit-frontend.json']},
        'browser_runner':'52 assertions passed; teardown interrupted; command exit 1',
        'physical_flash_cycles':0,'live_cloud_provider_runs':0}
(OUT/'audit-validation.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
print(json.dumps(result,indent=2))
if not result['all_39_sections'] or bad_links or changes or configuration_changes or result['coverage_total']!=result['source_files']:
    raise SystemExit(1)
