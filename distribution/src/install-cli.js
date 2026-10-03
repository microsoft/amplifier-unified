#!/usr/bin/env node
import {
  readInstallationConfiguration,
  installProductionDistribution,
} from "./installation.js";
const args = process.argv.slice(2);
if (args.length !== 2 || args[0] !== "--config")
  throw Error(
    "Usage: amplifier-unified-install --config /absolute/private-installation.json",
  );
try {
  const running = await installProductionDistribution(
    await readInstallationConfiguration(args[1]),
  );
  process.stdout.write(
    JSON.stringify({
      ready: true,
      version: running.initial.identity.version,
      release: running.initial.identity.id,
      serviceLifecycle: "not-configured",
    }) + "\n",
  );
  // Service stop/resume is a separate host-owned transition. Killing only this
  // supervisor would orphan the app; closing an active app could interrupt work.
  // Refuse these requests until a qualified owner can retain/release the fence.
  const refused = () =>
    process.stderr.write(
      JSON.stringify({
        status: "refused",
        code: "service_lifecycle_not_configured",
      }) + "\n",
    );
  process.on("SIGTERM", refused);
  process.on("SIGINT", refused);
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
