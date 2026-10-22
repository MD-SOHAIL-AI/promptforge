"""Read-only audit inventory; output files live beside this temporary helper."""
import ast
import collections
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).parent
EXCLUDE = {'node_modules', '.git', '.venv', '__pycache__', '.next', '.next-e2e', 'dist', 'build', 'public', 'audit', '.pytest_cache', 'test-results', 'playwright-report', 'workspace', 'assets'}
EXTENSIONS = {'.py', '.ts', '.tsx', '.mts', '.mjs', '.cjs', '.js', '.ps1', '.css'}
records = []
syntax_errors = []
searches = collections.defaultdict(list)
terms = ('TODO FIXME HACK DEPRECATED legacy fallback retry timeout circuit oauth codex gemini mcp skill memory subagent tool_call reasoning planner orchestrator autonomous sandbox shell subprocess exec eval dangerouslySetInnerHTML flash serial COM tty esptool avrdude openocd changeset workspace staging rollback approval token secret api_key').split()
def source_paths():
    for directory, dirs, files in os.walk(ROOT):
        dirs[:] = [name for name in dirs if name not in EXCLUDE and not name.startswith(('.forgex', '.promptforge', '.next'))]
        for name in files:
            yield Path(directory) / name

for p in sorted(source_paths()):
    if not p.is_file() or any(part in EXCLUDE or part.startswith('.forgex') or part.startswith('.promptforge') for part in p.relative_to(ROOT).parts):
        continue
    if p.suffix.lower() not in EXTENSIONS:
        continue
    data = p.read_bytes()
    text = data.decode('utf-8-sig', errors='replace')
    lines = text.splitlines()
    rel = p.relative_to(ROOT).as_posix()
    print('READ', rel, flush=True)
    entry = {'path': rel, 'loc': len(lines), 'sha256': hashlib.sha256(data).hexdigest(), 'kind': 'vendor' if rel.startswith(('third_party/', 'frontend/vendor/')) else 'test' if rel.startswith('tests/') or '.test.' in rel or '/e2e/' in rel else 'generated' if '.generated.' in rel or p.name == 'next-env.d.ts' else 'owned', 'symbols': [], 'imports': [], 'calls': [], 'routes': []}
    for term in terms:
        pattern = re.compile(re.escape(term), re.I)
        matches = [{'line': i, 'text': line[:300]} for i, line in enumerate(lines, 1) if pattern.search(line)]
        if matches:
            searches[term].append({'path': rel, 'matches': matches})
    if p.suffix == '.py':
        try:
            tree = ast.parse(text, filename=rel)
        except SyntaxError as exc:
            syntax_errors.append({'path': rel, 'line': exc.lineno, 'error': str(exc)})
        else:
            entry['purpose'] = ast.get_docstring(tree) or ''
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    entry['imports'].append(ast.unparse(node))
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    entry['symbols'].append({'name': node.name, 'line': node.lineno, 'end': node.end_lineno, 'size': node.end_lineno-node.lineno+1, 'type': type(node).__name__, 'purpose': ast.get_docstring(node) or ''})
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        for dec in node.decorator_list:
                            if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute) and dec.func.attr in {'get','post','put','patch','delete','websocket'}:
                                entry['routes'].append({'method': dec.func.attr.upper(), 'path': ast.literal_eval(dec.args[0]) if dec.args and isinstance(dec.args[0], ast.Constant) else '?', 'function': node.name, 'line': node.lineno})
                if isinstance(node, ast.Call):
                    entry['calls'].append({'name': ast.unparse(node.func), 'line': node.lineno})
    else:
        entry['imports'] = re.findall(r'(?:import|export)\s[^;\n]*?from\s+[\"\']([^\"\']+)[\"\']', text)
        entry['symbols'] = [{'name': m.group(1), 'line': text[:m.start()].count('\n')+1, 'type': 'js_symbol'} for m in re.finditer(r'(?:function|class|interface|type|const)\s+([A-Za-z_$][\w$]*)', text)]
    records.append(entry)
summary = {'files': len(records), 'loc': sum(r['loc'] for r in records), 'by_extension': dict(collections.Counter(Path(r['path']).suffix for r in records)), 'by_kind': dict(collections.Counter(r['kind'] for r in records)), 'by_top': {top: {'files': len(group), 'loc': sum(r['loc'] for r in group)} for top in sorted({r['path'].split('/')[0] for r in records}) for group in [[r for r in records if r['path'].split('/')[0] == top]]}, 'syntax_errors': syntax_errors}
(OUT/'inventory.json').write_text(json.dumps({'summary': summary, 'files': records}, indent=2), encoding='utf-8')
(OUT/'global-searches.json').write_text(json.dumps(searches, indent=2), encoding='utf-8')
(OUT/'source-hashes-before.json').write_text(json.dumps({r['path']: r['sha256'] for r in records}, indent=2), encoding='utf-8')
(OUT/'git-status-before.txt').write_text(subprocess.run(['git','status','--short'],cwd=ROOT,capture_output=True,text=True).stdout, encoding='utf-8')
print(json.dumps(summary, indent=2))
print('LARGEST OWNED FILES')
for r in sorted((r for r in records if r['kind']=='owned'), key=lambda r:r['loc'], reverse=True)[:30]:
    print(r['loc'], r['path'])
print('LARGEST PYTHON FUNCTIONS')
functions = [(s['size'], r['path'], s['name'], s['line'],s['end']) for r in records if r['kind']=='owned' for s in r['symbols'] if s['type'] in {'FunctionDef','AsyncFunctionDef'}]
for item in sorted(functions,reverse=True)[:30]: print(*item)
