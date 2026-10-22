import { createHash, randomUUID } from "node:crypto";
import { mkdir, open, readFile, readdir } from "node:fs/promises";
import { DatabaseSync } from "node:sqlite";
import path from "node:path";

import type { JsonObject, SessionEvent } from "./contracts";

const GENESIS = "0".repeat(64);
const SESSION_ID = /^[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}$/;

function canonical(value: unknown): string {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  return `{${Object.entries(value as Record<string, unknown>)
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([key, item]) => `${JSON.stringify(key)}:${canonical(item)}`)
    .join(",")}}`;
}

function checksum(event: Omit<SessionEvent, "checksum">): string {
  return createHash("sha256").update(canonical(event), "utf8").digest("hex");
}

export class EventStoreError extends Error {
  constructor(readonly code: string, message = code) {
    super(message);
    this.name = "EventStoreError";
  }
}

export class SessionEventStore {
  private readonly chains = new Map<string, Promise<unknown>>();

  constructor(private readonly root: string) {}

  async initialize(): Promise<void> {
    await mkdir(this.root, { recursive: true });
  }

  async listSessions(): Promise<string[]> {
    await this.initialize();
    const entries = await readdir(this.root, { withFileTypes: true });
    return entries
      .filter((entry) => entry.isFile() && entry.name.endsWith(".jsonl"))
      .map((entry) => entry.name.slice(0, -6))
      .filter((id) => SESSION_ID.test(id))
      .sort();
  }

  async read(sessionId: string): Promise<SessionEvent[]> {
    const file = this.file(sessionId);
    let raw: string;
    try {
      raw = await readFile(file, "utf8");
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === "ENOENT") return [];
      throw error;
    }
    const events: SessionEvent[] = [];
    let previous = GENESIS;
    for (const [index, line] of raw.split(/\r?\n/).entries()) {
      if (!line) continue;
      let event: SessionEvent;
      try {
        event = JSON.parse(line) as SessionEvent;
      } catch {
        throw new EventStoreError("SESSION_LOG_INVALID_JSON", `Invalid JSON at line ${index + 1}`);
      }
      const { checksum: actual, ...unsigned } = event;
      if (
        event.schemaVersion !== 1
        || event.sessionId !== sessionId
        || event.sequence !== events.length + 1
        || event.previousChecksum !== previous
        || checksum(unsigned) !== actual
      ) {
        throw new EventStoreError("SESSION_LOG_INVARIANT_FAILED", `Corrupt event at sequence ${event.sequence}`);
      }
      events.push(Object.freeze(event));
      previous = actual;
    }
    return events;
  }

  append(sessionId: string, type: string, payload: JsonObject, runId: string | null = null, operationId?: string): Promise<SessionEvent> {
    const prior = this.chains.get(sessionId) ?? Promise.resolve();
    const next = prior.then(() => this.appendUnlocked(sessionId, type, payload, runId, operationId));
    const settled = next.then(() => undefined, () => undefined);
    this.chains.set(sessionId, settled);
    void settled.then(() => { if (this.chains.get(sessionId) === settled) this.chains.delete(sessionId); });
    return next;
  }

  private async appendUnlocked(sessionId: string, type: string, payload: JsonObject, runId: string | null, operationId?: string): Promise<SessionEvent> {
    await this.initialize();
    this.file(sessionId); // Validate before opening any state file.
    // SQLite's OS lock is released when the owner dies. JSONL remains the
    // event format; this database only serializes publishers across processes.
    const lock = new DatabaseSync(this.file(sessionId) + ".writer.sqlite3");
    try { lock.exec("PRAGMA busy_timeout=0; BEGIN IMMEDIATE"); }
    catch (error) {
      lock.close();
      if ((error as Error).message.includes("locked")) throw new EventStoreError("SESSION_WRITER_LOCKED");
      throw error;
    }
    try {
      return await this.appendOwned(sessionId, type, payload, runId, operationId);
    } finally {
      lock.exec("ROLLBACK");
      lock.close();
    }
  }

  private async appendOwned(sessionId: string, type: string, payload: JsonObject, runId: string | null, operationId?: string): Promise<SessionEvent> {
    if (!type || type.length > 160 || !/^[a-z0-9][a-z0-9./_-]*$/i.test(type)) {
      throw new EventStoreError("SESSION_EVENT_TYPE_INVALID");
    }
    const events = await this.read(sessionId);
    const eventId = operationId ? createHash("sha256").update(operationId).digest("hex") : randomUUID();
    const saved = events.find((event) => event.eventId === eventId);
    if (saved) {
      if (saved.type !== type || saved.runId !== runId || canonical(saved.payload) !== canonical(payload)) {
        throw new EventStoreError("SESSION_OPERATION_CONFLICT");
      }
      return saved;
    }
    const unsigned: Omit<SessionEvent, "checksum"> = {
      schemaVersion: 1,
      eventId,
      sessionId,
      runId,
      sequence: events.length + 1,
      timestamp: new Date().toISOString(),
      type,
      payload: structuredClone(payload),
      previousChecksum: events.at(-1)?.checksum ?? GENESIS,
    };
    const event: SessionEvent = Object.freeze({ ...unsigned, checksum: checksum(unsigned) });
    await this.initialize();
    const handle = await open(this.file(sessionId), "a", 0o600);
    try {
      await handle.writeFile(`${canonical(event)}\n`, { encoding: "utf8" });
      await handle.sync();
    } finally {
      await handle.close();
    }
    return event;
  }

  private file(sessionId: string): string {
    if (!SESSION_ID.test(sessionId)) throw new EventStoreError("SESSION_ID_INVALID");
    return path.join(this.root, `${sessionId}.jsonl`);
  }
}
