import type { ForgeAgentMessage, ForgeChatSession } from "../types/index.ts";

const terminal = new Set(["completed", "failed", "cancelled", "interrupted"]);
export function mergeChatMessage(
  previous: ForgeAgentMessage | undefined,
  incoming: ForgeAgentMessage,
): ForgeAgentMessage {
  if (!previous) return incoming;
  const before = String(previous.metadata?.status ?? "");
  const after = String(incoming.metadata?.status ?? "");
  if (terminal.has(before) && after === "streaming") return previous;
  if (
    before === "streaming" &&
    after === "streaming" &&
    previous.content.length > incoming.content.length
  )
    return previous;
  return incoming;
}

export function mergeChatSession(
  previous: ForgeChatSession | null,
  incoming: ForgeChatSession,
): ForgeChatSession {
  if (previous?.session_id !== incoming.session_id) return incoming;
  const known = new Map(
    previous.messages.map((message) => [message.message_id, message]),
  );
  const messages = incoming.messages.map((message) => {
    const old = known.get(message.message_id);
    known.delete(message.message_id);
    return mergeChatMessage(old, message);
  });
  // A GET/POST snapshot can finish after a newer SSE packet.
  messages.push(...known.values());
  return {
    ...incoming,
    messages,
    status: messages.some((message) => message.metadata?.status === "streaming")
      ? "running"
      : "idle",
  };
}
