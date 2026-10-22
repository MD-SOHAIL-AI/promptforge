import assert from "node:assert/strict";
import test from "node:test";

import { coerceToError, isBenignRuntimeRejection, redact, serializeError } from "./diagnostics.ts";

test("serializes ordinary renderer errors with stack and status fields", () => {
  const error = new Error("request failed") as Error & { status?: number; code?: string; body?: unknown };
  error.name = "ForgeXApiError";
  error.status = 502;
  error.code = "BAD_GATEWAY";
  error.body = { detail: "provider failed" };

  const serialized = serializeError(error);

  assert.equal(serialized.name, "ForgeXApiError");
  assert.equal(serialized.message, "request failed");
  assert.equal(serialized.status, 502);
  assert.equal(serialized.error_code, "BAD_GATEWAY");
  assert.match(String(serialized.stack), /request failed/);
});

test("serializes object promise rejection reasons instead of producing object tags", () => {
  const serialized = serializeError({ message: "provider denied", status: 401, code: "AUTH", detail: { retry: false } });

  assert.equal(serialized.name, "NonErrorRejection");
  assert.equal(serialized.message, "provider denied");
  assert.equal(serialized.status, 401);
  assert.equal(serialized.error_code, "AUTH");
});

test("serializes nested object rejection messages", () => {
  const serialized = serializeError({
    error: {
      message: "openrouter could not route the selected model",
      code: 404,
    },
    status: 404,
  });

  assert.equal(serialized.message, "openrouter could not route the selected model");
  assert.equal(serialized.status, 404);
});

test("coerces object promise rejection reasons into readable errors", () => {
  const coerced = coerceToError({
    error: {
      message: "openrouter could not route the selected model",
      code: 404,
    },
    status: 404,
  });

  assert.ok(coerced instanceof Error);
  assert.equal(coerced.name, "NonErrorRejection");
  assert.equal(coerced.message, "openrouter could not route the selected model");
});

test("classifies Monaco loader cancellation as benign", () => {
  assert.equal(isBenignRuntimeRejection({ type: "cancelation", msg: "operation is manually canceled" }), true);
  assert.equal(isBenignRuntimeRejection({ type: "cancelation", msg: "different cancellation" }), false);
  assert.equal(isBenignRuntimeRejection({ message: "operation is manually canceled" }), false);
});

test("redacts renderer diagnostic secrets", () => {
  const safe = redact({
    Authorization: "Bearer sk-or-v1-supersecret1234567890",
    body: "key sk-test1234567890abc",
    cookie: "session=abc",
  }) as Record<string, unknown>;

  assert.equal(safe.Authorization, "[redacted]");
  assert.equal(safe.cookie, "[redacted]");
  assert.equal(safe.body, "key [redacted]");
});
