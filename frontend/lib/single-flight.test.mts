import test from "node:test";
import assert from "node:assert/strict";
import { SingleFlight } from "./single-flight.ts";

test("overlapping reads share a request, then refresh with fresh data", async () => {
  const flight = new SingleFlight();
  let release!: (value: number) => void;
  let requests = 0;
  const read = () => { requests++; return new Promise<number>(resolve => {release = resolve;}); };
  const a = flight.run("graph:r", read);
  const b = flight.run("graph:r", read);
  assert.equal(a, b);
  await Promise.resolve();
  release(1);
  assert.deepEqual(await Promise.all([a, b]), [1, 1]);
  assert.equal(requests, 1);
  assert.equal(await flight.run("graph:r", async () => 2), 2);
});

test("a failed request can be retried and different runs do not share reads", async () => {
  const flight = new SingleFlight();
  await assert.rejects(flight.run("r1", async () => {throw new Error("offline");}), /offline/);
  assert.deepEqual(await Promise.all([flight.run("r1", async () => 1), flight.run("r2", async () => 2)]), [1, 2]);
});
