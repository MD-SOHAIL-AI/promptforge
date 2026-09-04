import type { ExecutionEvent } from "@/types";

export type ExecutionSocketState = "connecting" | "reconnecting" | "open" | "closed" | "error";

export interface ExecutionSocketOptions {
  taskId: string;
  after?: number;
  onEvent: (event: ExecutionEvent) => void;
  onStateChange?: (state: ExecutionSocketState) => void;
}

export function websocketBaseUrl() {
  const configured = process.env.NEXT_PUBLIC_PROMPTFORGE_WS_URL;
  if (configured) return configured.replace(/\/$/, "");
  if (typeof window !== "undefined") {
    const desktopUrl = window.forgexDesktop?.getBackendUrl?.();
    if (desktopUrl) return httpToWebSocketUrl(desktopUrl);
  }
  const backendUrl = process.env.NEXT_PUBLIC_PROMPTFORGE_BACKEND_URL;
  if (backendUrl) return httpToWebSocketUrl(backendUrl);
  if (typeof window === "undefined") return "ws://127.0.0.1:8000";
  return `${window.location.protocol === "https:" ? "wss" : "ws"}://${window.location.hostname}:8000`;
}

function httpToWebSocketUrl(value: string): string {
  const url = new URL(value);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return url.toString().replace(/\/$/, "");
}

function terminal(event: ExecutionEvent) {
  return event.event === "WORKFLOW_COMPLETED" || event.event === "WORKFLOW_FAILED" || event.event === "WORKFLOW_CANCELLED";
}

export function createExecutionSocket({ taskId, after = 0, onEvent, onStateChange }: ExecutionSocketOptions) {
  let socket: WebSocket | null = null;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  let manuallyClosed = false;
  let terminalReceived = false;
  let reconnectAttempt = 0;
  let lastSequence = after;

  const scheduleReconnect = () => {
    if (manuallyClosed || terminalReceived || reconnectTimer) return;
    if (reconnectAttempt >= 6) {
      onStateChange?.("error");
      return;
    }
    reconnectAttempt += 1;
    onStateChange?.("reconnecting");
    const delay = Math.min(5_000, 250 * 2 ** (reconnectAttempt - 1));
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null;
      connect();
    }, delay);
  };

  const connect = () => {
    if (manuallyClosed || terminalReceived) return;
    onStateChange?.(reconnectAttempt ? "reconnecting" : "connecting");
    socket = new WebSocket(`${websocketBaseUrl()}/ws/execution/${encodeURIComponent(taskId)}?after=${lastSequence}`);
    socket.addEventListener("open", () => {
      reconnectAttempt = 0;
      onStateChange?.("open");
    });
    socket.addEventListener("message", (message) => {
      try {
        const event = JSON.parse(message.data as string) as ExecutionEvent;
        if (event.sequence <= lastSequence) return;
        if (event.sequence > lastSequence + 1) {
          socket?.close(4001, "Execution event sequence gap");
          return;
        }
        lastSequence = event.sequence;
        terminalReceived = terminal(event);
        onEvent(event);
      } catch {
        socket?.close(4002, "Invalid execution event");
      }
    });
    socket.addEventListener("error", scheduleReconnect);
    socket.addEventListener("close", () => {
      socket = null;
      if (manuallyClosed || terminalReceived) {
        onStateChange?.("closed");
      } else {
        scheduleReconnect();
      }
    });
  };

  connect();

  return () => {
    manuallyClosed = true;
    if (reconnectTimer) clearTimeout(reconnectTimer);
    reconnectTimer = null;
    if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) {
      socket.close(1000, "Workspace closed");
    }
    socket = null;
  };
}
