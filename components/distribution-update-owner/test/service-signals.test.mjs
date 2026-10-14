import test from "node:test";
import assert from "node:assert/strict";
import { attachSupervisorSignalHandlers } from "../dist/index.js";
const tick = () => new Promise((resolve) => setImmediate(resolve));
test("foreground signals refuse before launch or without qualified lifecycle and do not force retry", async (t) => {
  let current,
    stops = 0,
    closes = 0,
    ended = 0,
    resolveStop;
  const codes = [],
    receipt = new Promise((resolve) => (resolveStop = resolve));
  const remove = attachSupervisorSignalHandlers({
    current: () => current,
    onStopped: () => ended++,
    onRefused: (code) => codes.push(code),
  });
  t.after(remove);
  process.emit("SIGTERM");
  assert.deepEqual(codes, ["service_starting_or_unconfirmed"]);
  current = {
    stopService: async () => {
      throw Error("service_lifecycle_not_configured");
    },
    close: async () => closes++,
  };
  process.emit("SIGINT");
  await tick();
  assert.equal(codes.at(-1), "service_lifecycle_not_configured");
  assert.equal(closes, 0);
  current = {
    stopService: async () => {
      stops++;
      return receipt;
    },
    close: async () => closes++,
  };
  process.emit("SIGINT");
  process.emit("SIGTERM");
  assert.equal(stops, 1);
  resolveStop({ status: "unknown" });
  await tick();
  assert.equal(codes.at(-1), "service_stop_unconfirmed");
  assert.equal(closes, 0);
  assert.equal(ended, 0);
  current.stopService = async () => ({ status: "stopped" });
  process.emit("SIGTERM");
  await tick();
  assert.equal(closes, 1);
  assert.equal(ended, 1);
});
