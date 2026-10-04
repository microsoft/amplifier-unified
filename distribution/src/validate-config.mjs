import {isAbsolute,join,resolve,relative} from 'node:path';
export const OWNERS=['portability','capability:attachments','workspaces','native-admin','application-updates','capability:voice','capability:connectors','notifications','diagnostics','capability:observations','capability:coordination','capability:worktrees','capability:publishing','capability:recall','capability:feedback','recovery','history-import','history-cleanup','managed-files','manual-preview-ingress'];
const same=(a,b)=>JSON.stringify([...a].sort())===JSON.stringify([...b].sort());
export const FRESH_COMPOSITION_SCHEMA='unified-full-owner-fresh-composition-v1';
export function assertFreshInstallationLayout(c,directory){
 const expected={sourceDirectory:'source',claimDirectory:'claim',supervisorDirectory:'supervisor',
  supervisorDiscoveryFile:'supervisor.json',supervisorTokenFile:'supervisor-token',
  hostDiscoveryFile:'host-control.json',hostTokenFile:'host-token'};
 if(c.schema!==FRESH_COMPOSITION_SCHEMA||typeof directory!=='string'||!isAbsolute(directory)||resolve(directory)!==directory||
    Object.entries(expected).some(([key,name])=>c.authority?.[key]!==join(directory,name))||
    c.application?.stateDirectory!==join(directory,'application')||
    c.application?.manualIngress?.stateDirectory!==join(directory,'ingress')||c.receiptDirectory!==join(directory,'receipts'))
  throw Error('fresh_installation_layout_mismatch');
}
export function validInitialRelease(value){
 return !!value&&typeof value==='object'&&!Array.isArray(value)&&
  Object.keys(value).sort().join(',')==='id,revision,version'&&
  typeof value.id==='string'&&/^[A-Za-z0-9][A-Za-z0-9._:-]{0,99}$/.test(value.id)&&
  typeof value.version==='string'&&value.version.length<=80&&/^\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?$/.test(value.version)&&
  typeof value.revision==='string'&&/^[a-f0-9]{40,64}$/.test(value.revision);
}
/** Exact runtime census; never infer expected owners from those that appeared. */
export function assertOwnerCensus(actual,expected){
 if(!Array.isArray(actual)||!Array.isArray(expected)||new Set(actual).size!==actual.length||
    new Set(expected).size!==expected.length||!same(actual,expected))throw Error('configured_owner_census_mismatch');
}
export function inspectConfig(c,{launch=false}={}){
 const issues=[],add=(ok,id)=>{if(!ok)issues.push(id);},a=c.application??{},args=a.catalogProcess?.args??[];
 const fresh=c.schema===FRESH_COMPOSITION_SCHEMA;
 add(fresh||c.schema==='unified-full-owner-composition-v1','schema');
 if(fresh){
  add(validInitialRelease(c.release?.initial)&&!Object.hasOwn(c.release??{},'prepared'),'fresh-initial-release');
  add(!['sourceUnit','observerPython','bindings','bootstrapRecovery'].some(key=>Object.hasOwn(c,key)),'fresh-source-authority-forbidden');
  add(typeof c.authority?.installationId==='string'&&/^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/.test(c.authority.installationId),'planned-installation-id');
 }else add(!Object.hasOwn(c.release??{},'initial'),'fresh-schema-required');
 add(same(c.expectedOwners??[],OWNERS)&&new Set(c.expectedOwners??[]).size===20,'owner-census');
 add(a.recovery?.authorization==='local-account'&&a.recovery?.credentials===false&&!!a.conversationPresentation,'presentation-account-policy');
 for(const key of ['workspaces','nativeAdmin','maintenance','applicationUpdates','media','mcp','notifications','diagnostics','operations','coordination','worktrees','publishing','recall','feedback','portability','recovery','historyImport','historyCleanup','managedFiles'])add(!!a[key],'owner-config:'+key);
 add(!args.includes('--scan-on-start')&&!args.includes('--hint-directory')&&!a.catalogProcess?.maintenanceMs,'catalog-competing-writers');
 for(const key of ['--scan-interval','--workspace-check-interval'])add(args.includes(key)&&args[args.indexOf(key)+1]==='0','catalog-schedule:'+key);
 add(a.gateway?.host==='127.0.0.1','loopback-backend');
 add(a.nativeAdmin?.engine==='amplifier'&&JSON.stringify(a.portability?.engines)===JSON.stringify(['amplifier']),'native-transfer-binding');
 add(c.release?.entrypoint==='src/full-owner-launcher.mjs','signed-entrypoint');
 add(c.authority?.installationId&&c.authority?.ownerId&&c.authority?.dataScope,'stable-service-identity');
 const paths=[c.authority?.sourceDirectory,c.authority?.claimDirectory,c.authority?.supervisorDirectory,c.authority?.supervisorDiscoveryFile,c.authority?.supervisorTokenFile,c.authority?.hostDiscoveryFile,c.authority?.hostTokenFile,a.manualIngress?.stateDirectory];
 add(paths.every(p=>typeof p==='string'&&(isAbsolute(p)||p.startsWith('UNRESOLVED_'))),'private-authority-paths');
 add(new Set(paths).size===paths.length,'distinct-authority-paths');
 if(c.authority?.sourceDirectory&&!c.authority.sourceDirectory.startsWith('UNRESOLVED_'))add(Buffer.byteLength(join(c.authority.sourceDirectory,'control.sock'))<=100,'source-socket-path-length');
 for(const p of paths.filter(p=>p&&!p.startsWith('UNRESOLVED_')))for(const q of paths.filter(q=>q&&!q.startsWith('UNRESOLVED_')&&q!==p)){const r=relative(resolve(p),resolve(q));add(r==='..'||r.startsWith('../')||isAbsolute(r),'nonoverlapping-authority-paths');}
 const tokens=[...new Set(JSON.stringify(c).match(/UNRESOLVED_[A-Z0-9_]+/g)??[])].sort();
 if(launch){
  add(tokens.length===0,'unresolved-substitutions');
  add(c.review?.status==='approved'&&c.review?.combinedLinuxReceiptSha256?.match(/^[a-f0-9]{64}$/),'reviewed-combined-qualification');
  add(c.review?.catalogWriterConcurrency==='qualified','catalog-concurrency-qualification');
  add(c.review?.nativeModeProjection==='qualified','native-mode-qualification');
  add(c.review?.operationsPortabilityResolver==='qualified','python-resolver-qualification');
  if(!fresh)add(c.release?.prepared?.identity?.digest?.match(/^[a-f0-9]{64}$/),'prepared-release');
 }
 return {valid:issues.length===0,launchable:launch&&issues.length===0,issues:[...new Set(issues)],unresolved:tokens};
}
export function requireLaunchConfig(c){const result=inspectConfig(c,{launch:true});if(!result.valid)throw Error('launch_configuration_not_qualified');return c;}
