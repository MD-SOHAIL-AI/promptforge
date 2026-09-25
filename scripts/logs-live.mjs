import fs from "node:fs";
import path from "node:path";
import readline from "node:readline";

const logPath = path.resolve(process.env.FORGEX_LIVE_LOG_PATH?.trim() || path.join(".forgex", "logs", "live-runtime.jsonl"));
let offset = 0;
let watching = null;
let polling = null;

fs.mkdirSync(path.dirname(logPath), { recursive: true });
if (!fs.existsSync(logPath)) fs.closeSync(fs.openSync(logPath, "a"));

console.log(`[logs:live] tailing ${logPath}`);
offset = fs.statSync(logPath).size;

readNewLines();
watch();

process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);

function watch() {
  closeWatchers();
  try {
    watching = fs.watch(path.dirname(logPath), (eventType, filename) => {
      if (!filename || String(filename) === path.basename(logPath)) {
        handleChange();
      }
    });
  } catch {
    polling = setInterval(handleChange, 1000);
  }
}

function handleChange() {
  try {
    const stat = fs.statSync(logPath);
    if (stat.size < offset) offset = 0;
    readNewLines();
  } catch {
    offset = 0;
  }
}

function readNewLines() {
  let stream;
  try {
    const stat = fs.statSync(logPath);
    if (stat.size <= offset) return;
    stream = fs.createReadStream(logPath, { encoding: "utf8", start: offset, end: stat.size - 1 });
    offset = stat.size;
  } catch {
    return;
  }
  const lines = readline.createInterface({ input: stream, crlfDelay: Infinity });
  lines.on("line", (line) => {
    if (line.trim()) console.log(line);
  });
}

function closeWatchers() {
  if (watching) watching.close();
  watching = null;
  if (polling) clearInterval(polling);
  polling = null;
}

function shutdown() {
  closeWatchers();
  process.exit(0);
}
