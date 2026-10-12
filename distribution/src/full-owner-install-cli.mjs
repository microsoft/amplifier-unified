#!/usr/bin/env node
import {
  readInstallationConfiguration,
} from "./installation.js";
import {installFullOwnerDistribution} from './full-owner-installation.mjs';
import {inspectInstallationBackupConfiguration,startInstallationBackupService} from './installation-backup-service.js';
import { attachSupervisorSignalHandlers, startupFailureFrom } from "@amplifier/unified-distribution-update-owner";
const args = process.argv.slice(2);
if (![2,4].includes(args.length) || args[0] !== "--config" || args.length===4&&args[2]!=='--backup-config')
  throw Error(
    "Usage: amplifier-unified-install-full-owner --config /absolute/private-installation.json [--backup-config /absolute/private-backup.json]",
  );
let running,backup;
attachSupervisorSignalHandlers({
  current: () => running?.supervisor,
  onStopped: () => {void Promise.resolve(backup?.close()).then(()=>process.exit(0),()=>process.exit(1));},
  onRefused: (code) =>
    process.stderr.write(JSON.stringify({ status: "refused", code }) + "\n"),
});
try {
  const input=await readInstallationConfiguration(args[1]);
  // Refuse malformed backup authority before allocating or claiming a new
  // installation. Loading executable adapters waits until the app is installed.
  if(args[3]){
    const composition=await readInstallationConfiguration(input.compositionFile);
    await inspectInstallationBackupConfiguration({directory:input.directory,configurationFile:args[3],application:composition.application});
  }
  running = await installFullOwnerDistribution(input);
  if(args[3])backup=await startInstallationBackupService({directory:input.directory,configurationFile:args[3],
    getSupervisor:()=>running.supervisor,replaceSupervisor:next=>{running.supervisor=next;}});
  process.stdout.write(
    JSON.stringify({
      ready: true,
      backupConfigured: !!backup,
      version: running.initial.identity.version,
      release: running.initial.identity.id,
      serviceLifecycle: running.supervisor.service
        ? "configured"
        : "not-configured",
    }) + "\n",
  );
} catch (error) {
  // The private directory retains exact attempt/claim evidence. Do not delete it
  // or guess whether a launch occurred after a lost readiness response.
  process.stderr.write(
    JSON.stringify({
      ready: false,
      code: "installation_unconfirmed",
      startupFailure: startupFailureFrom(error),
      guidance:
        "Inspect private installation receipts before retrying; existing state was preserved.",
    }) + "\n",
  );
  process.exitCode = 1;
}
