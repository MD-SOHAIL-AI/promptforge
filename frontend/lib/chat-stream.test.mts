import assert from "node:assert/strict";
import test from "node:test";
import { mergeChatMessage, mergeChatSession } from "./chat-stream.ts";

const message = (content: string, status: string) => ({
  message_id: "m",
  role: "assistant" as const,
  content,
  created_at: "2026-01-01",
  metadata: { status },
});
test("replayed streaming snapshots cannot undo a completed response", () => {
  const done = message("Complete answer", "completed");
  assert.equal(mergeChatMessage(done, message("", "streaming")), done);
  assert.equal(
    mergeChatMessage(
      message("Partial answer", "streaming"),
      message("Par", "streaming"),
    ).content,
    "Partial answer",
  );
});
test("cancellation freezes text and retries use a new message identity", () => {
  const done = mergeChatMessage(
    message("partial", "streaming"),
    message("partial", "cancelled"),
  );
  assert.equal(mergeChatMessage(done, message("late", "streaming")), done);
  assert.equal(
    mergeChatMessage(undefined, {
      ...message("retry", "streaming"),
      message_id: "retry",
    }).content,
    "retry",
  );
});
test("snapshot from another session never inherits old messages", () => {
  const a = {
    session_id: "a",
    kind: "chat" as const,
    project_id: null,
    title: "A",
    messages: [message("old", "completed")],
    status: "idle",
  };
  const b = { ...a, session_id: "b", messages: [] };
  // Fixtures contain only fields consumed by this reducer.
  assert.deepEqual(mergeChatSession(a as never, b as never).messages, []);
  const late = { ...a, messages: [] };
  assert.equal(
    mergeChatSession(a as never, late as never).messages[0].content,
    "old",
  );
});
