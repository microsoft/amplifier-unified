/** Private bootstrap for an owned Node distribution process. Stop travels over
 * the inherited IPC channel; no supervisor ever signals a remembered PID. */
import { pathToFileURL } from "node:url";
import { isAbsolute } from "node:path";
import { classifyStartupError, startupFailure } from "./startup-diagnostics.js";
const key = process.env.AMPLIFIER_OWNED_CONTROL_TOKEN;
const instanceId = process.env.AMPLIFIER_DISTRIBUTION_INSTANCE_ID;
const entry = process.argv[2];
if (!process.send || !key || !instanceId || !entry || !isAbsolute(entry))
  throw Error("owned_bootstrap_invalid");
delete process.env.AMPLIFIER_OWNED_CONTROL_TOKEN;
let stopping = false;
process.on("message", (value) => {
  const message = value as Record<string, unknown>;
  if (
    !message ||
    message.schema !== "distribution-owned-child-v1" ||
    message.key !== key ||
    message.instanceId !== instanceId ||
    message.operation !== "stop" ||
    stopping
  )
    return;
  stopping = true;
  // This signal targets this executing process, never a PID from discovery or
  // a receipt. The host has already held and retired every participant.
  setImmediate(() => process.kill(process.pid, "SIGTERM"));
});
// Losing the supervisor is not permission to stop active work or self-restart.
process.on("disconnect", () => {});
process.argv = [process.execPath, entry, ...process.argv.slice(3)];
process.send({
  schema: "distribution-owned-child-v1",
  operation: "owned",
  key,
  instanceId,
});
try {
  await import(pathToFileURL(entry).href);
} catch (error) {
  // Ownership is announced before application initialization. Preserve a small
  // authenticated failure report after that handshake instead of making the
  // parent guess from process loss. Never send the exception or its log text.
  await new Promise<void>((resolve) => {
    if (!process.connected || !process.send) return resolve();
    process.send({ schema: "distribution-owned-child-v1", operation: "startup-failed", key, instanceId,
      failure: startupFailure(classifyStartupError(error), "initialization", "child-bootstrap") }, () => resolve());
  });
  process.exit(1);
}
