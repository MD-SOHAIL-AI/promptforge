import assert from 'node:assert/strict';
import {readFileSync, writeFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
import vm from 'node:vm';

const audit = path.dirname(fileURLToPath(import.meta.url));
const context = JSON.parse(readFileSync(path.join(audit, 'repair-context.json'), 'utf8'));
const source = readFileSync(path.join(context.repair_stage, 'src/network/web_server.cpp'), 'utf8');
const scripts = [...source.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)]
  .map(match => match[1]).filter(code => code.trim());
assert.ok(scripts.length);
for (const code of scripts) new vm.Script(code);
const relay = source.match(/function controlRelay\(type, state\) \{[\s\S]*?\n        \}\n/)[0];
assert.ok(source.includes('server.on("/api/relay", HTTP_POST'));
assert.ok(source.includes('request->hasParam("fan", true)') && source.includes('request->hasParam("light", true)'));
const calls = [];
const control = new Function('fetch', 'addLog', 'URLSearchParams', `${relay}; return controlRelay;`)(
  (url, options) => {
    calls.push({url, ...options});
    return Promise.resolve({json: () => Promise.resolve({status: 'ok'})});
  }, () => {}, URLSearchParams);
for (const type of ['fan', 'light']) {
  for (const state of [true, false]) {
    control(type, state);
    const call = calls.at(-1);
    assert.equal(call.url, '/api/relay');
    assert.equal(call.method, 'POST');
    assert.equal(call.headers['Content-Type'], 'application/x-www-form-urlencoded');
    assert.equal(new URLSearchParams(call.body).get(type), state ? 'on' : 'off');
  }
}
await new Promise(resolve => setImmediate(resolve));
const evidence = {embedded_scripts_parsed: scripts.length, relay_requests_verified: calls.length,
  method: 'POST', form_body_matches_server: true, hardware_verified: false};
writeFileSync(path.join(audit, 'dashboard-request-verification.json'), JSON.stringify(evidence, null, 2) + '\n');
console.log(JSON.stringify(evidence));
