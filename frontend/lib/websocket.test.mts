import assert from "node:assert/strict";
import test from "node:test";

import { createExecutionSocket } from "./websocket.ts";

type Listener = (event: { data?: string }) => void;

class FakeWebSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static instances: FakeWebSocket[] = [];
  readyState = FakeWebSocket.CONNECTING;
  listeners = new Map<string, Listener[]>();
  url: string;

  constructor(url: string) {
    this.url = url;
    FakeWebSocket.instances.push(this);
  }

  addEventListener(name: string, listener: Listener) {
    this.listeners.set(name, [...(this.listeners.get(name) ?? []), listener]);
  }

  emit(name: string, data?: string) {
    for (const listener of this.listeners.get(name) ?? []) listener({ data });
  }

  open() {
    this.readyState = FakeWebSocket.OPEN;
    this.emit("open");
  }

  close() {
    this.readyState = 3;
    this.emit("close");
  }
}

globalThis.WebSocket = FakeWebSocket as unknown as typeof WebSocket;

const event = (sequence: number, name = "PLAN_GENERATED") => JSON.stringify({
  sequence,
  event: name,
  timestamp: new Date().toISOString(),
  task_id: "task-replay",
  execution_id: "execution-1",
  workflow_correlation_id: "workflow-1",
  payload: {},
});

test("reconnects from the last received sequence", async () => {
  FakeWebSocket.instances = [];
  const received: number[] = [];
  const stop = createExecutionSocket({ taskId: "task-replay", onEvent: (item) => received.push(item.sequence) });
  const first = FakeWebSocket.instances[0];
  first.open();
  first.emit("message", event(1));
  first.close();
  await new Promise((resolve) => setTimeout(resolve, 320));

  assert.deepEqual(received, [1]);
  assert.match(FakeWebSocket.instances[1].url, /after=1$/);
  stop();
});

test("detects a sequence gap and requests replay", async () => {
  FakeWebSocket.instances = [];
  const received: number[] = [];
  const stop = createExecutionSocket({ taskId: "task-replay", onEvent: (item) => received.push(item.sequence) });
  const first = FakeWebSocket.instances[0];
  first.open();
  first.emit("message", event(1));
  first.emit("message", event(3));
  await new Promise((resolve) => setTimeout(resolve, 320));

  assert.deepEqual(received, [1]);
  assert.match(FakeWebSocket.instances[1].url, /after=1$/);
  stop();
});
