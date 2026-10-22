"""Human-readable complete descriptor and route inventories, with honest status."""
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
OUT=Path(__file__).parent
providers=json.loads((OUT/'providers.json').read_text())
routes=json.loads((OUT/'api-routes.json').read_text())
def safe(x):return str(x or 'SDK/default/options').replace('|','\\|').replace('\n',' ')
lines=['# Provider and API Inventories','',
       'This is descriptor/route evidence, not a list of successfully exercised external accounts. All default providers dispatch through Python OpenCodeProvider → Node engine. Ten named provider definitions have explicit API-key/local semantics; remaining descriptors derive from pinned Models.dev data. Unknown/special SDK compatibility and remote entitlements remain unverified. For every descriptor, retry/timeouts/usage/error normalization/admission use the common pipeline in sections 8–11 of the main report; default circuit enforcement is bypassed. Tool capability is per-model metadata, not a provider-wide guarantee.','',
       'Auth below is registry metadata. AWS Bedrock uses ambient/profile credential chains when no explicit key; Vertex uses Google Cloud ADC/project/service-account configuration. Neither is consumer subscription OAuth. GitHub Copilot uses a supplied bearer token, without a full account-login/refresh flow. Custom providers are user-created at runtime and therefore not a finite checked-in ID list.','',
       '## All provider descriptors','',
       '| ID | Registry auth | Environment names | Base endpoint | Catalog SDK | Catalog model count | Implementation / verification |',
       '|---|---|---|---|---|---:|---|']
for p in providers:
    env=list(dict.fromkeys(([p['env_key']] if p.get('env_key') else [])+p.get('catalog_env',[])))
    status='Explicit named definition; shared engine dispatch; account/live UNVERIFIED' if p['provider_id'] in {'openai','anthropic','gemini','deepseek','openrouter','groq','cerebras','nvidia_nim','ollama','lmstudio'} else 'Catalog descriptor + shared SDK/compatible dispatch; live UNVERIFIED'
    lines.append('| '+ ' | '.join([safe(p['provider_id']),safe(p['auth_type']),safe(', '.join(env) or 'none / options'),safe(p.get('base_url')),safe(p.get('catalog_sdk')),str(p['catalog_models']),status])+' |')
lines+=['','## Every declared backend API entry','',
        'All 124 extracted decorator entries are under backend. The route prefix below comes from its module APIRouter declaration, if present. Frontend /api/forgex is a proxy prefix, not part of the Python decorator. Decorator-declared routes are connected via create_app router inclusion; dynamic dependencies/services are described in the main report. The actual handler source link is provided rather than assuming frontend use.','',
        '| Method | Router-relative endpoint | Function | File / line |','|---|---|---|---|']
for r in sorted(routes,key=lambda x:(x['file'],x['line'])):
    path=r['router_prefix']+r['path']
    ref=str(ROOT/r['file']).replace('\\','/')+':'+str(r['line'])
    lines.append('| '+r['method']+' | `'+path+'` | `'+r['function']+'` | ['+r['file']+':'+str(r['line'])+']('+ref+') |')
(OUT/'Provider-and-API-Inventories.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
# Represent zero-result required searches explicitly, alongside existing matches.
p=OUT/'global-searches.json'; searches=json.loads(p.read_text())
for term in 'TODO FIXME HACK DEPRECATED legacy fallback retry timeout circuit oauth codex gemini mcp skill memory subagent tool_call reasoning planner orchestrator autonomous sandbox shell subprocess exec eval dangerouslySetInnerHTML flash serial COM tty esptool avrdude openocd changeset workspace staging rollback approval token secret api_key'.split():
    searches.setdefault(term,[])
p.write_text(json.dumps(searches,indent=2),encoding='utf-8')
print('Wrote',len(providers),'provider descriptors and',len(routes),'API entries.')
