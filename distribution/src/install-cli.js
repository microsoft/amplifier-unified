#!/usr/bin/env node
import {
  readInstallationConfiguration,
  installProductionDistribution,
} from "./installation.js";
import { attachSupervisorSignalHandlers } from "@amplifier/unified-distribution-update-owner";
const args = process.argv.slice(2);
if (args.length !== 2 || args[0] !== "--config")
  throw Error(
    "Usage: amplifier-unified-install --config /absolute/private-installation.json",
  );
let running;
attachSupervisorSignalHandlers({
  current: () => running?.supervisor,
  onStopped: () => process.exit(0),
  onRefused: (code) =>
    process.stderr.write(JSON.stringify({ status: "refused", code }) + "\n"),
});
try {
  running = await installProductionDistribution(
    await readInstallationConfiguration(args[1]),
  );
  process.stdout.write(
    JSON.stringify({
      ready: true,
      version: running.initial.identity.version,
      release: running.initial.identity.id,
      serviceLifecycle: running.supervisor.service
        ? "configured"
        : "not-configured",
    }) + "\n",
  );
} catch {
  // The private directory retains exact attempt/claim evidence. Do not delete it
  // or guess whether a launch occurred after a lost readiness response.
  process.stderr.write(
    JSON.stringify({
      ready: false,
      code: "installation_unconfirmed",
      guidance:
        "Inspect private installation receipts before retrying; existing state was preserved.",
    }) + "\n",
  );
  process.exitCode = 1;
}
