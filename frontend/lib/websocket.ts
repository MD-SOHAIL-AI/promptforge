import type { ExecutionEvent } from "@/types";

export interface ExecutionSocketOptions {
  taskId: string;
  after?: number;
  onEvent: (event: ExecutionEvent) => void;
  onStateChange?: (state: "connecting" | "open" | "closed" | "error") => void;
}

function websocketBaseUrl() {
  const configured = process.env.NEXT_PUBLIC_PROMPTFORGE_WS_URL;
  if (configured) return configured.replace(/\/$/, "");
  if (typeof window === "undefined") return "ws://127.0.0.1:8000";
  return `${window.location.protocol === "https:" ? "wss" : "ws"}://${window.location.hostname}:8000`;
}

export function createExecutionSocket({
  taskId,
  after = 0,
  onEvent,
  onStateChange,
}: ExecutionSocketOptions) {
  onStateChange?.("connecting");
  const socket = new WebSocket(
    `${websocketBaseUrl()}/ws/execution/${encodeURIComponent(taskId)}?after=${after}`,
  );

  socket.addEventListener("open", () => onStateChange?.("open"));
  socket.addEventListener("message", (message) => {
    try {
      onEvent(JSON.parse(message.data as string) as ExecutionEvent);
    } catch {
      onStateChange?.("error");
    }
  });
  socket.addEventListener("error", () => onStateChange?.("error"));
  socket.addEventListener("close", () => onStateChange?.("closed"));

  return () => {
    if (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING) {
      socket.close(1000, "Workspace closed");
    }
  };
}
