"""Restore only the build-generated next-env file after matching the baseline SHA."""
import hashlib
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
target=ROOT/'frontend/next-env.d.ts'
expected=json.loads((Path(__file__).parent/'source-hashes-before.json').read_text())['frontend/next-env.d.ts']
current=target.read_text()
names=[p.name for p in (ROOT/'frontend').iterdir() if p.is_dir() and p.name.startswith('.next')]
for name in names:
    text=current.replace('.next-e2e',name)
    for newline in ('\n','\r\n'):
        for bom in (b'',b'\xef\xbb\xbf'):
            data=bom+text.replace('\n',newline).encode()
            if hashlib.sha256(data).hexdigest()==expected:
                target.write_bytes(data)
                print('Restored exact baseline bytes:',name)
                raise SystemExit(0)
raise SystemExit('No matching baseline; did not overwrite pre-existing edits.')
