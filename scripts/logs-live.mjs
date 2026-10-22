import fs from "node:fs";
import path from "node:path";
import { StringDecoder } from "node:string_decoder";
import { pathToFileURL } from "node:url";

export class LiveLogTail {
  constructor(base, { fromStart = false } = {}) {
    this.base = path.resolve(base);
    this.cursors = new Map();
    this.pollSequence = 0;
    if (!fromStart) for (const file of this.sources()) {
      try {
        const stat = fs.statSync(file);
        this.cursors.set(this.identity(stat), { offset: stat.size, pending: "", decoder: new StringDecoder("utf8"), lastSeen: 0 });
      } catch { /* A concurrent rotation will be discovered on the next poll. */ }
    }
  }
  identity(stat) { return `${stat.dev}:${stat.ino}:${stat.birthtimeMs}`; }
  sources() {
    const parsed = path.parse(this.base);
    try {
      return fs.readdirSync(parsed.dir).filter(name => name === parsed.base || name === `${parsed.base}.1`
        || (name.startsWith(`${parsed.name}.backend-`) || name.startsWith(`${parsed.name}.desktop-`))
          && (name.endsWith(parsed.ext) || name.endsWith(`${parsed.ext}.1`)))
        .flatMap(name => {
          const file = path.join(parsed.dir, name);
          try {
            const stat = fs.statSync(file);
            return stat.isFile() ? [{ file, modified: stat.mtimeMs }] : [];
          } catch { return []; }
        }).sort((a, b) => b.modified - a.modified).slice(0, 128).map(item => item.file);
    } catch { return []; }
  }
  poll() {
    this.pollSequence++;
    const events = [];
    let malformed = 0;
    const present = new Set();
    for (const file of this.sources()) {
      let fd;
      try {
        fd = fs.openSync(file, "r");
        const stat = fs.fstatSync(fd);
        const key = this.identity(stat);
        present.add(key);
        let cursor = this.cursors.get(key);
        if (!cursor || stat.size < cursor.offset) {
          cursor = { offset: 0, pending: "", decoder: new StringDecoder("utf8"), lastSeen: this.pollSequence };
          this.cursors.set(key, cursor);
        }
        cursor.lastSeen = this.pollSequence;
        if (stat.size === cursor.offset) continue;
        const buffer = Buffer.alloc(Math.min(stat.size - cursor.offset, 2 * 1024 * 1024));
        const bytes = fs.readSync(fd, buffer, 0, buffer.length, cursor.offset);
        cursor.offset += bytes;
        const lines = (cursor.pending + cursor.decoder.write(buffer.subarray(0, bytes))).split("\n");
        cursor.pending = lines.pop();
        if (cursor.pending.length > 256 * 1024) { cursor.pending = ""; malformed++; }
        for (const line of lines) {
          if (!line.trim()) continue;
          try {
            const event = JSON.parse(line);
            if (!event || typeof event !== "object" || Array.isArray(event)) throw new Error("Invalid record");
            events.push(event);
          } catch { malformed++; }
        }
      } catch { /* Rotation is retried on the next poll. */ }
      finally { if (fd !== undefined) fs.closeSync(fd); }
    }
    // Keep identities briefly across a rename/discovery race, avoiding replay
    // of an old rotated file after one unsuccessful scan.
    for (const [key, cursor] of this.cursors) {
      if (!present.has(key) && this.pollSequence - cursor.lastSeen > 8) this.cursors.delete(key);
    }
    if (this.cursors.size > 256) {
      const oldest = [...this.cursors].sort((a, b) => a[1].lastSeen - b[1].lastSeen);
      for (const [key] of oldest.slice(0, this.cursors.size - 256)) this.cursors.delete(key);
    }
    events.sort((a, b) => String(a.timestamp ?? "").localeCompare(String(b.timestamp ?? "")));
    return { events, malformed };
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  const base = process.env.FORGEX_LIVE_LOG_PATH?.trim() || path.join(".forgex", "logs", "live-runtime.jsonl");
  const tail = new LiveLogTail(base);
  console.log(`[logs:live] merging instance logs and legacy logs in ${path.dirname(tail.base)}`);
  const timer = setInterval(() => {
    const batch = tail.poll();
    for (const event of batch.events) console.log(JSON.stringify(event));
    if (batch.malformed) console.warn(`[logs:live] skipped ${batch.malformed} malformed records`);
  }, 250);
  for (const signal of ["SIGINT", "SIGTERM"]) process.on(signal, () => { clearInterval(timer); process.exit(0); });
}
