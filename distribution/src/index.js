import {createHost,StdioCatalog} from '@amplifier/unified-host';
import {createNativeCapabilities,createPermissionsCapabilities,createMessageCapabilities,AdminConnection} from '@amplifier/unified-native-capabilities';
import {createResourcesCapability} from '@amplifier/unified-resources-capability';
import {createMaintenanceCapabilities} from '@amplifier/unified-maintenance-capability';
import {randomUUID,createHash} from 'node:crypto';
import {join,relative,isAbsolute} from 'node:path';
import {realpath,stat,readFile} from 'node:fs/promises';
import {createConfiguredStorageInventory} from './storage-inventory.js';
import {composeCapabilities} from './capabilities.js';
import {createGateway} from './gateway.js';
import {createClientMigration} from './client-migration.js';
import {composePortability} from './portability.js';
import {composeMedia} from './media.js';
import {composeMCP} from './mcp.js';
import {composeOperations,composePublishing,composeWorktrees,composeRecall} from './owners.js';
import {composeFeedback} from './feedback.js';
import {composeCoordination} from './coordination.js';
import {createApplicationUpdateCapabilities} from './application-updates.js';
import {createHistoryCleanupCapabilities} from './history-cleanup.js';
import {createRetentionProtection} from './retention-protection.js';
import {createManagedFilesCapabilities} from './managed-files.js';
import {createManagedFilesProtection} from './managed-files-protection.js';
import {bindRuntimeOwners,runtimeOwnerProvenance} from './runtime-owners.js';
import {composeWorkspaces} from './workspaces.js';
import {composeNotifications} from './notifications.js';
import {composeQuiescence,recoveryReleaseVerifier} from './quiescence.js';
import {composeRecovery} from './recovery.js';
import {composePresentation,presentationDiscoveryCatalog,reconstructPresentationMetadata} from './presentation.js';
import {composeHistory} from './history.js';
import {composeDiagnostics,diagnosticActionObserver} from './diagnostics.js';
import {composeMessages,messagePrincipalEngines} from './messages.js';
import {bindHeldOwnerSnapshots} from './owner-snapshots.js';
import {createTerminalOwner} from './terminal-owner.js';
import {renderTerminalInstaller as defaultTerminalInstaller} from './terminal-installer.js';
export {composeCapabilities,createGateway,createApplicationUpdateCapabilities};
export {createGitSourceResolver} from './source-tracking.js';
export {installProductionDistribution,readInstallationConfiguration} from './installation.js';

/** Public packages are composed here; none can access another owner's private state. */
export async function createDistribution(config,{authorize,authorizePublication,authorizeMaintenance,authorizeTransfer,authorizeFeedback,applicationUpdateSupervisor,authorizeRecovery,verifyQuiescenceRelease,serviceLifecycle,onMayBeIdle,capabilityOwners=[],createCapabilityOwners,runtimeOwnerBindings=[],beforeRecoveryMaintenance,renderTerminalInstaller=defaultTerminalInstaller}={}){
 if(!config.stateDirectory||!config.webDirectory||!config.defaultWorkspace)throw Error('stateDirectory, webDirectory and defaultWorkspace are required');
 const runtimeBindings=bindRuntimeOwners(runtimeOwnerBindings);
 if(beforeRecoveryMaintenance!==undefined&&(typeof beforeRecoveryMaintenance!=='function'||!config.recovery||!config.quiescence))throw Error('Trusted recovery coordinator requires configured recovery and quiescence');
 const engines=messagePrincipalEngines(config.engines,config.nativeAdmin?.engine,config.account);
 if(runtimeBindings.length&&!config.quiescence)throw Error('Runtime owner bindings require configured quiescence');
 const workspace=await realpath(config.defaultWorkspace),roots=await Promise.all(config.allowedWorkspaceRoots.map(root=>realpath(root)));
 if(!(await stat(workspace)).isDirectory()||!roots.some(root=>{const path=relative(root,workspace);return !path||path!=='..'&&!path.startsWith('../')&&!isAbsolute(path);}))throw Error('Default workspace must be within authorized roots');
 let host,gateway,admin,nativeCapabilities,catalog,workspaces,migration,capabilities,operations,recall,mcp,portability,coordination,recovery,quiescence,notifications,diagnostics,cleanup,retentionProtection,managedFiles,managedFilesProtection,ownerSnapshots,terminal,stopping=false;const token=randomUUID(),owners=[...capabilityOwners],storageOwners=new Map();
 const remember=(owner,name,configKey)=>{storageOwners.set(owner,{packageName:'@amplifier/'+name,configKey});return owner;};
 try{
 if(config.recovery&&!config.quiescence)throw Error('Recovery requires configured owner quiescence');
 if(config.historyCleanup&&(!config.quiescence||!config.catalogProcess))throw Error('History cleanup requires configured owner quiescence and catalog');
 if(config.managedFiles&&(!config.quiescence||!config.host?.managedSessionRoot))throw Error('Managed files require configured owner quiescence and an owned allocation root');
 if(config.quiescence&&config.portability&&(!config.nativeAdmin||config.portability.engines?.length!==1||config.portability.engines[0]!==config.nativeAdmin.engine))throw Error('Transfer quiescence requires the same single engine as native administration');
 const bindings=new Map(),mayBeIdle=()=>{try{onMayBeIdle?.();}catch{/* advisory only */}};
 const presentationConfig=config.conversationPresentation??config.host?.conversationPresentation;
 if(presentationConfig&&!config.catalogProcess)throw Error('Conversation presentation requires an indexed catalog');
 if(config.catalogProcess)catalog=new StdioCatalog(config.catalogProcess);
 const inspectSession=uri=>host.inspectSession(uri),invalidate=(topic,scope)=>{
  host?.invalidateCapability(topic,scope);
  if(['questions','runtime-control'].includes(topic)&&scope?.startsWith('ahp-session:/'))coordination?.changed(scope);
 };
 const admit=(method,...args)=>{if(stopping)throw Error('Distribution is stopping; new work was not admitted');return host[method](...args);};
 const resources=createResourcesCapability({...(config.portability?.resourcePayloads?{verifyTransferPayloadPlan:args=>{if(!portability?.owner)throw Error('Portability verifier unavailable');return portability.owner.verifyTransferPayloadPlan(args);}}:{}),directory:join(config.stateDirectory,'resources'),inspectSession,onChanged:invalidate,onMayBeIdle:mayBeIdle});owners.push(remember(resources,'unified-resources-capability','resources'));
 const ownerContext={
  account:config.account,onMayBeIdle:mayBeIdle,directory:join(config.stateDirectory,'capabilities'),inspectSession,
  readSessionContext:(...args)=>host.readSessionContext(...args),subscribeSession:(...args)=>host.observeSession(...args),
  inspectExportResource:(...args)=>host.inspectExportResource(...args),readExportResource:(...args)=>host.readExportResource(...args),
  readUserMessage:(...args)=>host.readUserMessage(...args),withSessionWorkspace:(...args)=>host.withSessionWorkspace(...args),
  readTaskState:(...args)=>host.readTaskState(...args),submitObservation:(...args)=>admit('submitObservation',...args),
  listRecallSources:(...args)=>host.listRecallSources(...args),inspectRecallSource:(...args)=>host.inspectRecallSource(...args),readRecallSource:(...args)=>host.readRecallSource(...args),
  relocateSession:(...args)=>admit('relocateSession',...args),directoryInUse:(...args)=>host.directoryInUse(...args),withDirectoryGuard:(...args)=>host.withDirectoryGuard(...args),
  createSession:(...args)=>admit('createSession',...args),submitScheduled:(...args)=>admit('submitScheduled',...args),submitQuestionAnswer:(...args)=>admit('submitQuestionAnswer',...args),waitForTurn:(...args)=>host.waitForTurn(...args),nativeControlExisting:(...args)=>host.nativeControlExisting(...args),
  clientPresent:async(session,clientId)=>(await host.listClientTools(session)).some(client=>client.clientId===clientId),
  delegate:async({session,...input})=>{await admit('submitTurn',session,input);return host.waitForTurn(session,input.commandId);},nativeControl:(...args)=>admit('nativeControl',...args),
  recordTranscript:async({session,callId,itemId,role,text,append,commandId})=>{const result=await host.nativeControl(session,'voice.transcript.record',{callId,itemId,role,text,append,commandId});if(result.recorded)await host.invalidateNativeHistory(session);return result;},
  invokeClientTool:(...args)=>host.invokeClientTool(...args),onInvalidate:invalidate,registerExternal:resources.registerExternal,
 };
 if(createCapabilityOwners)owners.push(...await createCapabilityOwners(ownerContext));
 if(config.workspaces){workspaces=await composeWorkspaces(config.workspaces,{...ownerContext,catalog:presentationConfig?presentationDiscoveryCatalog(catalog,()=>host):catalog,roots,defaultRoot:workspace});owners.push(remember(workspaces,'unified-workspace-capability','workspaces'));}
 if(config.nativeAdmin){
  const engine=engines.find(engine=>engine.id===config.nativeAdmin.engine);if(!engine)throw Error('Native administration engine is not configured');
  admin=new AdminConnection({...engine,onMayBeIdle:mayBeIdle,timeoutMs:config.nativeAdmin.timeoutMs??(config.maintenance||config.recovery?1_200_000:120_000),cwd:config.defaultWorkspace,resolveWorkspace:async context=>context.session?(await inspectSession(typeof context.session==='string'?context.session:context.session.uri)).workingDirectory:config.defaultWorkspace});
  const contextSession=async scope=>{
   const selected=await inspectSession(scope);
   if(selected.engineId!==engine.id||typeof selected.nativeSessionId!=='string'||!selected.nativeSessionId||typeof selected.workingDirectory!=='string'||!isAbsolute(selected.workingDirectory))throw Object.assign(Error('Context clear requires the admitted native engine and original history workspace'),{data:{executed:false,replayed:false,reason:'context-clear-authority'}});
   return selected;
  };
  nativeCapabilities=createNativeCapabilities({
   nativeControl:async(scope,operation,...args)=>{if(['context.clear.review','context.clear'].includes(operation))await contextSession(scope);return host.nativeControl(scope,operation,...args);},nativeAdmin:admin.perform,onInvalidate:invalidate,
   nativeBundleCommands:()=>admin.bundleCommandCapabilities(),
   nativeContextClear:()=>admin.contextClearCapabilities(),
   nativeContextReceipt:async(scope,commandId)=>{
    const selected=await contextSession(scope);
    // Passive lookup uses canonical history identity after relocation/restart.
    return admin.readContextReceipt({sessionId:selected.nativeSessionId,cwd:selected.workingDirectory,commandId});
   },
   nativeBundleReceipt:async(scope,commandId)=>{
    // The host has already admitted this capability under its authenticated
    // conversation context. Resolve native identity only through its public port.
    const selected=await inspectSession(scope);
    if(selected.engineId!==engine.id||typeof selected.nativeSessionId!=='string'||!selected.nativeSessionId||typeof selected.workingDirectory!=='string'||!isAbsolute(selected.workingDirectory))throw Object.assign(Error('Bundle receipt requires the admitted native engine and original history workspace'),{data:{executed:false,replayed:false,reason:'bundle-receipt-authority'}});
    // workingDirectory is immutable canonical history authority. A relocated
    // executionDirectory and a new current default cannot substitute for it.
    return admin.readBundleReceipt({sessionId:selected.nativeSessionId,cwd:selected.workingDirectory,commandId});
   },
  });
  // composeCapabilities snapshots manifests synchronously. Negotiate the real
  // configured peer first; older peers must never advertise receipt recovery.
  await nativeCapabilities.negotiateBundleCommands();
  await nativeCapabilities.negotiateContextClear?.();
  owners.push(remember(nativeCapabilities,'unified-native-capabilities','nativeAdmin'));bindings.set(nativeCapabilities,admin.quiescenceParticipant);
  const messages=await composeMessages(createMessageCapabilities,engine,{account:config.account,cwd:workspace,inspectSession,onInvalidate:invalidate,onMayBeIdle:mayBeIdle});
  // This passive connection closes its own intake and tracks in-flight calls.
  // Keep its distinct participant; the admin lease cannot fence another pipe.
  if(messages.ready)owners.push(remember(messages.capabilities,'unified-native-capabilities','nativeAdmin'));
  if(config.nativeAdmin.permissions===true){
   const permissions=createPermissionsCapabilities({nativeAdmin:admin.perform,inspectSession,onInvalidate:invalidate});
   owners.push(remember(permissions,'unified-native-capabilities','nativeAdmin'));bindings.set(permissions,admin.quiescenceParticipant);
  }
 }
 if(config.maintenance){
  if(!admin)throw Error('Native runtime maintenance requires explicitly configured native administration');
  const maintenance=createMaintenanceCapabilities({nativeAdmin:admin.perform,inspectResidentRuntime:(session,args)=>host.nativeControlExisting(session,'runtime.inspect',args),onInvalidate:invalidate,authorize:async context=>{if(context.account!==config.account)throw Error('Maintenance account mismatch');await authorizeMaintenance?.(context);}});owners.push(remember(maintenance,'unified-maintenance-capability','nativeAdmin'));bindings.set(maintenance,bindings.get(nativeCapabilities));
 }
 if(config.applicationUpdates)owners.push(remember(createApplicationUpdateCapabilities({supervisor:applicationUpdateSupervisor,directory:config.quiescence?join(config.stateDirectory,'capabilities','application-updates'):undefined,onMayBeIdle:mayBeIdle,onInvalidate:invalidate,authorize:async context=>{if(context.account!==config.account)throw Error('Application update account mismatch');await authorizeMaintenance?.(context);}}),'unified-distribution-update-owner','applicationUpdates'));
 if(config.media)owners.push(remember(await composeMedia(config.media,ownerContext,{nativeAdmin:admin}),'unified-media-capability','media'));
 if(config.mcp){mcp=composeMCP(config.mcp,ownerContext);owners.push(remember(mcp,'unified-mcp-capabilities','mcp'));ownerContext.qualifiedObservation=(...args)=>mcp.qualifiedObservation(...args);}
 if(config.notifications){notifications=await composeNotifications(config.notifications,ownerContext);owners.push(remember(notifications,'unified-notifications-capability','notifications'));ownerContext.notifySchedule=notifications.notifySchedule;}
 if(config.terminal){
  if(typeof config.terminal!=='object'||Array.isArray(config.terminal)||Object.keys(config.terminal).some(key=>!['origin','artifacts'].includes(key)))throw Error('Unexpected Terminal configuration fields');
  if(config.terminal.origin!==config.gateway?.origin)throw Error('Terminal origin must match the configured public gateway origin');
  terminal=createTerminalOwner({...config.terminal,directory:join(config.stateDirectory,'capabilities','terminal'),account:config.account,renderInstaller:renderTerminalInstaller,onMayBeIdle:mayBeIdle,onInvalidate:invalidate});owners.push(remember(terminal,'unified','terminal'));
 }
 if(config.diagnostics){diagnostics=await composeDiagnostics(config.diagnostics,ownerContext);owners.push(remember(diagnostics,'unified-diagnostics-capability','diagnostics'));}
 if(config.operations){operations=await composeOperations(config.operations,ownerContext);owners.push(remember(operations,'unified-operations-capabilities','operations'));}
 if(config.coordination){coordination=await composeCoordination(config.coordination,ownerContext,{host:()=>host,operations,admit});owners.push(remember(coordination,'unified-coordination-capability','coordination'));}
 if(config.worktrees){const composed=await composeWorktrees(config.worktrees,ownerContext);owners.push(remember(composed.owner,'unified-worktree-capability','worktrees'));roots.push(composed.executionRoot);}
 if(config.publishing)owners.push(remember(await composePublishing(config.publishing,ownerContext,authorizePublication),'unified-publishing-capability','publishing'));
 if(config.recall){recall=await composeRecall(config.recall,ownerContext);owners.push(remember(recall,'unified-recall-capability','recall'));}
 if(config.feedback)owners.push(remember(await composeFeedback(config.feedback,ownerContext,authorizeFeedback),'unified-feedback-capability','feedback'));
 if(config.portability){
  const evidenceOwners=new Map([['unified.resources',resources],...(operations?[['unified.operations',operations]]:[])]);
  const omissions=owners.filter(owner=>![...evidenceOwners.values()].includes(owner)).map(owner=>({topics:Object.keys(owner.manifest?.topics??{}),reason:'This owner has no qualified transfer evidence export; its source records remain on the source host'}));
  portability=await composePortability(config.portability,ownerContext,{engines,roots,host:()=>host,evidenceOwners,omissions,authorizeTransfer});owners.push(remember(portability.owner,'unified-portability-capability','portability'));
  if(config.host?.transferIdentity&&config.host.transferIdentity!==portability.identity)throw Error('Configured transfer identity differs from the trusted owner');
 }
 const nativeAuthority=config.recovery?.nativeAuthority??config.account+':'+config.nativeAdmin?.engine;
 if(config.recovery){recovery=await composeRecovery(config.recovery,ownerContext,{admin,host:()=>host,engineId:config.nativeAdmin?.engine,nativeAuthority,authorize:authorizeRecovery,presentation:!!presentationConfig,appResetOwners:[notifications?.appReset,config.applicationUpdates&&applicationUpdateSupervisor?.appReset].filter(Boolean),
  beforeMaintenance:beforeRecoveryMaintenance?async context=>{
   const pending=[];let active=true,error;
   try{await beforeRecoveryMaintenance(Object.freeze({context,stageOwnerSnapshot:({ownerId,...input})=>{
    if(!active||!ownerSnapshots||ownerId!==ownerSnapshots.participant.id||Object.keys(input).some(key=>!['snapshotCommandId','directory','privateContentReviewed'].includes(key)))throw Error('Exact configured held-owner snapshot request required');
    const work=ownerSnapshots.stageHeld({...input,fenceId:context.fenceId,commandId:context.commandId});pending.push(work);void work.catch(()=>{});return work;
   }}));}catch(cause){error=cause;}finally{active=false;}
   // A trusted coordinator cannot accidentally detach capture from the held
   // job by forgetting to await its returned promise.
   const settled=await Promise.allSettled(pending);if(error)throw error;
   const failed=settled.find(row=>row.status==='rejected');if(failed)throw failed.reason;
   if(settled.some(row=>row.value?.status!=='sealed'))throw Error('Required owner snapshot remains unsealed');
  }:undefined});owners.push(remember(recovery,'unified-recovery-capability','recovery'));}
 else if(presentationConfig){recovery=composePresentation(ownerContext,{host:()=>host,authorize:authorizeRecovery});owners.push(remember(recovery,'unified-recovery-capability','conversationPresentation'));}
 if(config.historyImport)owners.push(remember(composeHistory(config.historyImport,ownerContext,{admin,host:()=>host,engineId:config.nativeAdmin?.engine}),'unified-history-capability','historyImport'));
 if(config.historyCleanup){
  cleanup=createHistoryCleanupCapabilities({host:()=>host,protection:()=>retentionProtection,directory:join(config.stateDirectory,'capabilities','history-cleanup'),onMayBeIdle:mayBeIdle,onInvalidate:invalidate,authorize:async context=>{if(context.account!==config.account)throw Error('History cleanup account mismatch');await authorizeMaintenance?.(context);}});
  owners.push(remember(cleanup,'unified','historyCleanup'));
 }
 if(config.managedFiles){
  managedFiles=createManagedFilesCapabilities({host:()=>host,protection:()=>managedFilesProtection,directory:join(config.stateDirectory,'capabilities','managed-files'),onMayBeIdle:mayBeIdle,onInvalidate:invalidate,authorize:async(context,operation)=>{if(context.account!==config.account)throw Error('Managed-files account mismatch');await authorizeMaintenance?.(context,operation);}});
  owners.push(remember(managedFiles,'unified','managedFiles'));
 }
 capabilities=composeCapabilities(owners,{account:config.account,...(diagnostics?{onAction:diagnosticActionObserver(diagnostics,workspace)}:{})});
 // Transfer peers close their local intake before the shared admin owner holds
 // the one exclusive native-home writer lease.
 const quiescenceOwners=portability?[portability.owner,...owners.filter(owner=>owner!==portability.owner)]:owners;
 if(config.quiescence&&operations&&typeof operations.inspectOwnerSnapshot==='function'&&typeof operations.readOwnerSnapshot==='function'){
  const id='capability:'+Object.keys(operations.manifest.topics).sort()[0];
  ownerSnapshots=bindHeldOwnerSnapshots({provider:operations,participant:operations.quiescenceParticipant(id),withMaintenance:(input,work)=>host.withQuiescenceMaintenance(input,work)});
  bindings.set(operations,ownerSnapshots.participant);
 }
 if(config.quiescence)quiescence=composeQuiescence(config.quiescence,quiescenceOwners,{bindings,runtimeOwners:runtimeBindings.map(binding=>binding.owner),serviceLifecycle,onMayBeIdle:mayBeIdle,verifyRelease:recoveryReleaseVerifier({...config.quiescence,nativeAuthority,recovery:()=>recovery,fallback:verifyQuiescenceRelease})});
 if(cleanup){
  // The cleanup facade owns this in-flight forwarding call. The native typed
  // hide owns its family/native-home leases. Every other configured product
  // owner must independently close intake and inspect future references.
  const excluded=new Set([cleanup.quiescenceParticipant.id,admin?.quiescenceParticipant?.id]);
  const byId=new Map(quiescence.participants.map(p=>[p.id,p]));
  const participants=quiescence.requiredOwners.filter(id=>!excluded.has(id)).map(id=>byId.get(id)??{id});
  retentionProtection=createRetentionProtection({directory:join(config.stateDirectory,'capabilities','retention-protection'),instanceId:quiescence.instanceId,dataScope:quiescence.dataScope,participants,readItemReceipt:commandId=>host.retentionItemReceipt(commandId)});
 }
 if(managedFiles){
  // The initiating facade owns forwarding; the typed native operation owns its
  // canonical family lease. All other product owners inspect file references
  // under their own distinct managed-files hold, including the cleanup facade.
  const excluded=new Set([managedFiles.quiescenceParticipant.id,admin?.quiescenceParticipant?.id]);
  const byId=new Map(quiescence.participants.map(p=>[p.id,p]));
  const participants=quiescence.requiredOwners.filter(id=>!excluded.has(id)).map(id=>byId.get(id)??{id});
  managedFilesProtection=createManagedFilesProtection({directory:join(config.stateDirectory,'capabilities','managed-files-protection'),instanceId:quiescence.instanceId,dataScope:quiescence.dataScope,participants,readEffectReceipt:(session,commandId)=>host.readManagedFilesEffectReceipt(session,commandId),onMayBeIdle:mayBeIdle});
 }
  if(config.legacyClientState){
   if(config.legacyClientState.account!==config.account)throw Error('Legacy client storage must explicitly belong to the authenticated account');
   migration=createClientMigration({...config.legacyClientState,resolveNative:catalog?params=>catalog.request('resolveNative',{...params,allowedWorkspaceRoots:roots}):undefined});
  }
  const gatewayConfig={...config.gateway,account:config.account,webDirectory:config.webDirectory,hostToken:token,authorize};
  host=await createHost({...config.host,...(presentationConfig?{conversationPresentation:{reconstructMetadata:presentationConfig.reconstructMetadata??reconstructPresentationMetadata(catalog)}}:{}),...(quiescence?{quiescence}:{}),...(retentionProtection?{retentionProtection}:{}),...(managedFilesProtection?{managedFilesProtection}:{}),...(portability?{transferIdentity:portability.identity}:{}),stateDirectory:join(config.stateDirectory,'host'),engines,allowedWorkspaceRoots:roots,defaultWorkingDirectory:workspace,host:'127.0.0.1',port:0,bearerToken:token,allowedOrigins:[],capabilities,catalog,clientMetadata:migration?.metadata,resourceProviders:[...capabilities.resources,...(migration?[migration.resourceProvider]:[])],
   resolvePromptAttachment:(context,attachment)=>resources.resolvePromptAttachment(context,attachment,{mode:config.engines.find(engine=>engine.id===context.engineId)?.attachmentMode??'inline'}),
   nativeHostCapabilities:{version:1,name:'Amplifier Unified',appControl:{operations:['get_state','list_actions','dispatch'],guidance:'Get session state to discover attached client tools. Shared actions have exact schemas in list_actions. Private selection, drafts and media belong to the explicitly chosen client; inspect its standard client tool before applying a local action. No background mirroring of private UI state occurs.'},features:{...(operations?{operations:true,questions:true}:{}),...(operations&&mcp?{observation:true}:{}),...(recall?{memory:true}:{})}},
   turnSettled:async event=>{if(stopping)return;await notifications?.turnSettled(event);if(recall&&event.status==='completed'&&['ui','user'].includes(event.inputOrigin))await recall.idle(event.session);},
   agentStopped:async event=>{if(operations)await operations.interrupted(event.session);for(const owner of owners)await owner.agentStopped?.(event);},
   nativeEvent:async(context,params)=>{if(params.event?.type==='workers.changed')coordination?.changed(context.session);if(params.event?.type==='configuration.pending')nativeCapabilities?.invalidate(context.session,['configuration']);for(const owner of owners)await owner.nativeEvent?.(context,params);},
   nativeHostRequest:async(context,params)=>{
    const input=params.args??{};
    if(params.operation==='memory.context'){
     if(!recall)throw Error('Memory authority is not configured');
     return recall.memoryContext(context.session,input);
    }
    if(params.operation==='questions.admit'){
     if(!operations)throw Error('Question authority is not configured');
     const ids=input.questionIds;
     if(!Array.isArray(ids)||ids.length>32||ids.some(id=>typeof id!=='string'||!id||id.length>200)||new Set(ids).size!==ids.length)throw Error('Invalid required question IDs');
     for(const id of ids)await operations.questionDependency(context.session,id);
     return {admitted:true,questionIds:ids};
    }
    if(params.operation==='questions.delivery.admit'){
     if(!operations)throw Error('Question authority is not configured');
     return operations.authorizeQuestionDelivery(context.session,input);
    }
    if(params.operation==='observation.admit'){
     if(!operations||!mcp)throw Error('Qualified observation authority is not configured');
     return operations.authorizeObservation(context.session,input);
    }
    if(params.operation==='operations.observe'){
     if(!operations)throw Error('Operation observation is not configured');
     return operations.observe(context.session,input.runtimeSessionId,input.event);
    }
    if(params.operation==='get_state'){
     const path=input.path??'session';
     if(path==='session')return {...await inspectSession(context.session),clients:await host.listClientTools(context.session),availableTopics:Object.keys(capabilities.manifest.topics)};
     if(path==='clients')return {clients:await host.listClientTools(context.session)};
     const topic=capabilities.manifest.topics[path];if(!topic)throw Error('Unknown scoped state path');const scope=topic.scope==='host'?'host':context.session,uri=new URL(topic.uri);uri.searchParams.set('scope',scope);return capabilities.read({uri:uri.href,topic:path,scope,clientId:''},{origin:'agent',session:context.session});
    }
    if(params.operation==='list_actions'){
     const actions={...await capabilities.getActionSchemas(),'clients.invoke':{description:'Invoke one standard client-provided tool on an explicitly attached target. Inspect its advertised schema and current revision first.',schema:{type:'object',properties:{clientId:{type:'string'},toolName:{type:'string'},args:{type:'object'}},required:['clientId','toolName','args'],additionalProperties:false}}};
     return {actions:Object.entries(actions).filter(([name])=>name.startsWith(input.prefix??'')).map(([name,value])=>({name,...value}))};
    }
    if(params.operation!=='dispatch')throw Error('Host operation is not advertised');
    if(stopping)throw Error('Distribution is stopping; new actions were not admitted');
    const operation=input.action,args=input.args??{};
    if(operation==='clients.invoke')return host.invokeClientTool(context.session,args.clientId,args.toolName,args.args??{});
    const advertised=capabilities.manifest.actions[operation];
    if(!advertised)throw Error('Host capability is not advertised: '+operation);
    if(args.sessionId&&args.sessionId!==context.session)throw Error('Native capability cannot select another conversation');
    if(input.id!==undefined&&(typeof input.id!=='string'||!input.id||input.id.length>256))throw Error('Native command identity must be a bounded string');
    const commandId=input.id?'native:'+context.nativeSessionId+':'+input.id:randomUUID();
    if(commandId.length>256)throw Error('Namespaced native command identity exceeds the advertised limit');
    const channel=capabilities.manifest.topics[advertised.topic]?.scope==='host'?'ahp-root://':context.session;
    return host.invokeCapability({channel,topic:advertised.topic,operation:advertised.operation,version:1,args,commandId},{actorId:'agent:'+context.nativeSessionId,origin:'agent',session:context.session});
   }
  });
  const handlers=owners.flatMap(owner=>owner.httpHandlers??(owner.handleMedia?[{matches:path=>path.startsWith('/media/'),handle:owner.handleMedia}]:[]));
  gateway=await createGateway({...gatewayConfig,hostUrl:host.url,handlers});
  host.config.allowedOrigins=[gateway.url];
  for(const owner of owners)await owner.initializeOrigin?.(gateway.url);
  for(const owner of owners)await owner.start?.();
  let closing;
  return {url:gateway.url,host,capabilities,resources,quiescence,...(terminal?{terminalAccess:terminal.terminalAccess}:{}),
   async stageOwnerSnapshot({ownerId,...input}){if(!ownerSnapshots||ownerId!==ownerSnapshots.participant.id)throw Error('Configured owner snapshot unavailable');return ownerSnapshots.stage(input);},
   async inspectOwnerSnapshot({ownerId,commandId}){if(!ownerSnapshots||ownerId!==ownerSnapshots.participant.id)throw Error('Configured owner snapshot unavailable');return ownerSnapshots.inspect(commandId);},
   async storageInventory(options={}){
   const provenance=JSON.parse(await readFile(new URL('../components.json',import.meta.url),'utf8'));
   if(cleanup||managedFiles||terminal){
    // These forwarding owners ship in the root package. Bind their protection,
    // composition and provenance helpers on this cold path, not another owner's SHA.
    const hash=createHash('sha256');
    for(const path of ['index.js','history-cleanup.js','retention-protection.js','managed-files.js','managed-files-protection.js','facade-fence.js','quiescence.js','runtime-owners.js','storage-inventory.js','owner-snapshots.js','recovery.js',...(terminal?['terminal-owner.js','terminal-artifacts.js','terminal-installer.js','terminal-install-client.mjs']:[])]){const bytes=await readFile(new URL(path,import.meta.url));hash.update(path+'\0'+bytes.length+'\0');hash.update(bytes);}
    provenance.components['@amplifier/unified']={revision:'sha256:'+hash.digest('hex')};
   }
   const runtimeInventory=await runtimeOwnerProvenance(runtimeBindings,config,provenance.components),ownerProvenance={...runtimeInventory.ownerProvenance};
   for(const owner of owners){const declaration=storageOwners.get(owner),topic=Object.keys(owner.manifest?.topics??{}).sort()[0],id=quiescence?.coverage.capabilities[topic];if(declaration&&id&&!ownerProvenance[id])ownerProvenance[id]=declaration;}
   return createConfiguredStorageInventory(config,{...options,omissions:[...(options.omissions??[]),...runtimeInventory.omissions],quiescence,components:provenance.components,ownerProvenance,nativeCaptureOwnerId:admin?.quiescenceParticipant.id});
  },close(){if(!closing){stopping=true;ownerSnapshots?.close();closing=(async()=>{await gateway.close();await workspaces?.close();await host.close();await admin?.close();await capabilities.close();retentionProtection?.close();managedFilesProtection?.close();migration?.close();})();}return closing;}};
 }catch(error){stopping=true;await gateway?.close();await workspaces?.close();await host?.close();if(!host)await catalog?.close();await admin?.close();await Promise.allSettled(owners.map(owner=>owner.close?.()));retentionProtection?.close();managedFilesProtection?.close();migration?.close();throw error;}
}

export {readInstalledServiceConfiguration,openInstalledService,connectInstalledService} from "./service.js";

export {createStorageInventory,createConfiguredStorageInventory,validateStorageInventory} from './storage-inventory.js';

export {createInstalledStorageInventory} from './installed-storage-inventory.js';
export {createInstallationArchive,createCoherentInstallationArchive,inspectInstallationArchive,restoreInstallationArchive,stageNativeInstallationArtifact} from './installation-archive.js';
export {createNativeCoherentCaptureAdapter} from './native-capture.js';

export {inspectFullOwnerInstallation,installFullOwnerDistribution} from './full-owner-installation.mjs';
