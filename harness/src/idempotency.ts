import { createHash } from "node:crypto";
import { mkdir, open, readFile, rename } from "node:fs/promises";
import path from "node:path";

import type { JsonValue } from "./contracts";

const KEY = /^[A-Za-z0-9][A-Za-z0-9._:-]{7,255}$/;

interface IdempotencyRecord {
  schemaVersion: 1;
  key: string;
  fingerprint: string;
  result: JsonValue;
  status?: "started" | "completed";
}

export class IdempotencyError extends Error {
  constructor(readonly code: string, message: string) {
    super(message);
    this.name = "IdempotencyError";
  }
}

/** Durable replay protection for mutating RPC requests.
 *
 * Calls for one key are serialized in-process. Successful results are written
 * atomically and survive desktop restarts. Reusing a key for a different
 * request fails closed instead of replaying the wrong result.
 */
export class IdempotencyStore {
  private readonly pending = new Map<string, { fingerprint: string; promise: Promise<JsonValue> }>();

  constructor(private readonly root: string) {}

  async initialize(): Promise<void> {
    await mkdir(this.root, { recursive: true });
  }

  execute(key: string, fingerprint: string, operation: () => Promise<JsonValue>, reconcile?: () => Promise<JsonValue>): Promise<JsonValue> {
    if (!KEY.test(key)) throw new IdempotencyError("RPC_IDEMPOTENCY_KEY_INVALID", "Invalid idempotency key");
    const existing = this.pending.get(key);
    if (existing) {
      if (existing.fingerprint !== fingerprint) {
        throw new IdempotencyError("RPC_IDEMPOTENCY_CONFLICT", "Idempotency key is in use for a different request");
      }
      return existing.promise;
    }
    const task = this.executeUnlocked(key, fingerprint, operation, reconcile).finally(() => this.pending.delete(key));
    this.pending.set(key, { fingerprint, promise: task });
    return task;
  }

  private async executeUnlocked(key: string, fingerprint: string, operation: () => Promise<JsonValue>, reconcile?: () => Promise<JsonValue>): Promise<JsonValue> {
    const saved = await this.read(key);
    if (saved) {
      if (saved.fingerprint !== fingerprint) {
        throw new IdempotencyError("RPC_IDEMPOTENCY_CONFLICT", "Idempotency key was already used for a different request");
      }
      if (saved.status !== "started") return structuredClone(saved.result);
      if (!reconcile) throw new IdempotencyError("RPC_OUTCOME_UNKNOWN", "An interrupted operation requires reconciliation; it was not replayed.");
      const result = await reconcile();
      await this.write({ schemaVersion: 1, key, fingerprint, result: structuredClone(result), status: "completed" });
      return result;
    }
    await this.initialize();
    const intent = await open(this.file(key), "wx", 0o600).catch(() => {
      throw new IdempotencyError("RPC_OUTCOME_UNKNOWN", "An operation reservation already exists or could not be persisted.");
    });
    try {
      await intent.writeFile(JSON.stringify({ schemaVersion: 1, key, fingerprint, result: null, status: "started" }), "utf8");
      await intent.sync();
    } finally { await intent.close(); }
    const result = await operation();
    await this.write({ schemaVersion: 1, key, fingerprint, result: structuredClone(result), status: "completed" });
    return result;
  }

  private async read(key: string): Promise<IdempotencyRecord | null> {
    try {
      const value = JSON.parse(await readFile(this.file(key), "utf8")) as Partial<IdempotencyRecord>;
      if (value.schemaVersion !== 1 || value.key !== key || typeof value.fingerprint !== "string" || !("result" in value) || (value.status !== undefined && value.status !== "started" && value.status !== "completed")) {
        throw new IdempotencyError("RPC_IDEMPOTENCY_RECORD_INVALID", "Stored idempotency record is invalid");
      }
      return value as IdempotencyRecord;
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === "ENOENT") return null;
      if (error instanceof IdempotencyError) throw error;
      throw new IdempotencyError("RPC_IDEMPOTENCY_RECORD_INVALID", "Stored idempotency record could not be read");
    }
  }

  private async write(record: IdempotencyRecord): Promise<void> {
    await this.initialize();
    const target = this.file(record.key);
    const temporary = `${target}.${process.pid}.${Date.now()}.tmp`;
    const handle = await open(temporary, "wx", 0o600);
    try {
      await handle.writeFile(JSON.stringify(record), "utf8");
      await handle.sync();
    } finally {
      await handle.close();
    }
    await rename(temporary, target);
  }

  private file(key: string): string {
    return path.join(this.root, `${createHash("sha256").update(key, "utf8").digest("hex")}.json`);
  }
}
