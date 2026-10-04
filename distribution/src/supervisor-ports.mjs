// Trusted local factory. Merely inspecting this module opens no owner/ledger.
import {readFile} from 'node:fs/promises';
import {createGitSourceResolver} from '@amplifier/unified';
import {connectHostControlFile} from '@amplifier/unified-distribution-update-owner';
import {requireLaunchConfig} from './validate-config.mjs';
export async function createSupervisorPorts(config){
 const c=requireLaunchConfig(JSON.parse(await readFile(config.applicationConfiguration,'utf8')));
 const host=connectHostControlFile(c.authority.hostDiscoveryFile,c.authority.dataScope);
 return {resolveSources:createGitSourceResolver({sources:c.sourcePolicy}),inspect:()=>host.inspect(),admitRestart:host.admitRestart,reconcileAdmission:host.reconcileAdmission,inspectAdmissionFence:host.inspectAdmissionFence,inspectAdmissionAbort:host.inspectAdmissionAbort,abortAdmission:host.abortAdmission,service:host.service,onIdle:host.onIdle,close:()=>host.close?.()};
}
export default createSupervisorPorts;
