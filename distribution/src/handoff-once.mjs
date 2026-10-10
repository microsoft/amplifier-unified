// Operator-only. This performs a one-use handoff; it is NOT a status/retry tool.
// Do not invoke during staging. The source must already be instrumented and ready.
import {readFile,writeFile} from 'node:fs/promises';
import {requireLaunchConfig} from './validate-config.mjs';
if(process.argv[2]!=='--execute-reviewed-handoff')throw Error('explicit_operator_handoff_required');
const c=requireLaunchConfig(JSON.parse(await readFile(process.argv[3],'utf8')));
const command=JSON.parse(await readFile(process.argv[4],'utf8'));
if(command.reviewStatus!=='approved'||!command.commandId||!command.stoppedCommandId||!command.expected||!command.receiptFile)throw Error('reviewed_handoff_command_required');
const api=await import('@amplifier/unified-distribution-update-owner');
const {createSupervisorPorts}=await import('./supervisor-ports.mjs');
const configuration=JSON.parse(await readFile(c.supervisorConfiguration,'utf8'));
const ports=await createSupervisorPorts({applicationConfiguration:process.argv[3]});
const supervisor=await api.runSupervisor(configuration,{startInitial:false,ports});
const source=api.createManualSystemdHandoffSource({sourceDirectory:c.authority.sourceDirectory,claimDirectory:c.authority.claimDirectory,bindings:c.bindings,observer:api.createLinuxSystemdSourceObserver({unit:c.sourceUnit,python:c.observerPython})});
// No catch/retry or implicit shutdown: an uncertain handoff stays owned/preserved.
await api.launchExistingStateHandoff({destination:supervisor.service,source,bindings:c.bindings,command:{commandId:command.commandId,stoppedCommandId:command.stoppedCommandId,expected:command.expected,target:c.release.prepared.identity,participantIds:c.expectedOwners}});
const result=await supervisor.service.waitFor(command.commandId);
await writeFile(command.receiptFile,JSON.stringify(result)+'\n',{flag:'wx',mode:0o600});
if(result.status!=='ready'||result.admissionSettlement?.state!=='settled')throw Error('handoff_requires_passive_inspection');
// Remain as supervisor owner. Do not close it, terminate it, or reinterpret a
// controller shell disconnect as permission to stop/relaunch its owned child.
process.stdout.write('handoff_ready_and_settled\n');
