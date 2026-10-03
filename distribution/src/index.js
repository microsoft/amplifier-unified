import {createHost,StdioCatalog} from '@amplifier/unified-host';
import {createNativeCapabilities,AdminConnection} from '@amplifier/unified-native-capabilities';
import {createResourcesCapability} from '@amplifier/unified-resources-capability';
import {createMaintenanceCapabilities} from '@amplifier/unified-maintenance-capability';
import {randomUUID} from 'node:crypto';
import {join,relative,isAbsolute} from 'node:path';
import {realpath,stat} from 'node:fs/promises';
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
import {composeWorkspaces} from './workspaces.js';
import {composeNotifications} from './notifications.js';
import {composeQuiescence,recoveryReleaseVerifier} from './quiescence.js';
import {composeRecovery} from './recovery.js';
export {composeCapabilities,createGateway,createApplicationUpdateCapabilities};
export {createGitSourceResolver} from './source-tracking.js';

/** Public packages are composed here; none can access another owner's private state. */
export async function createDistribution(config,{authorize,authorizePublication,authorizeMaintenance,authorizeTransfer,authorizeFeedback,applicationUpdateSupervisor,authorizeRecovery,verifyQuiescenceRelease,onMayBeIdle,capabilityOwners=[],createCapabilityOwners}={}){
 if(!config.stateDirectory||!config.webDirectory||!config.defaultWorkspace)throw Error('stateDirectory, webDirectory and defaultWorkspace are required');
 const workspace=await realpath(config.defaultWorkspace),roots=await Promise.all(config.allowedWorkspaceRoots.map(root=>realpath(root)));
 if(!(await stat(workspace)).isDirectory()||!roots.some(root=>{const path=relative(root,workspace);return !path||path!=='..'&&!path.startsWith('../')&&!isAbsolute(path);}))throw Error('Default workspace must be within authorized roots');
 let host,gateway,admin,nativeCapabilities,catalog,workspaces,migration,capabilities,operations,recall,mcp,portability,coordination,recovery,quiescence,notifications,stopping=false;const token=randomUUID(),owners=[...capabilityOwners];
 try{
 if(config.recovery&&!config.quiescence)throw Error('Recovery requires configured owner quiescence');
 if(config.quiescence&&config.portability&&(!config.nativeAdmin||config.portability.engines?.length!==1||config.portability.engines[0]!==config.nativeAdmin.engine))throw Error('Transfer quiescence requires the same single engine as native administration');
 const bindings=new Map(),mayBeIdle=()=>{try{onMayBeIdle?.();}catch{/* advisory only */}};
 if(config.catalogProcess)catalog=new StdioCatalog(config.catalogProcess);
 const inspectSession=uri=>host.inspectSession(uri),invalidate=(topic,scope)=>{
  host?.invalidateCapability(topic,scope);
  if(['questions','runtime-control'].includes(topic)&&scope?.startsWith('ahp-session:/'))coordination?.changed(scope);
 };
 const admit=(method,...args)=>{if(stopping)throw Error('Distribution is stopping; new work was not admitted');return host[method](...args);};
 const resources=createResourcesCapability({directory:join(config.stateDirectory,'resources'),inspectSession,onChanged:invalidate,onMayBeIdle:mayBeIdle});owners.push(resources);
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
 if(config.workspaces){workspaces=await composeWorkspaces(config.workspaces,{...ownerContext,catalog,roots,defaultRoot:workspace});owners.push(workspaces);}
 if(config.nativeAdmin){
  const engine=config.engines.find(engine=>engine.id===config.nativeAdmin.engine);if(!engine)throw Error('Native administration engine is not configured');
  admin=new AdminConnection({...engine,onMayBeIdle:mayBeIdle,timeoutMs:config.nativeAdmin.timeoutMs??(config.maintenance?1_200_000:120_000),cwd:config.defaultWorkspace,resolveWorkspace:async context=>context.session?(await inspectSession(typeof context.session==='string'?context.session:context.session.uri)).workingDirectory:config.defaultWorkspace});
  nativeCapabilities=createNativeCapabilities({nativeControl:(...args)=>host.nativeControl(...args),nativeAdmin:admin.perform,onInvalidate:invalidate});owners.push(nativeCapabilities);bindings.set(nativeCapabilities,admin.quiescenceParticipant);
 }
 if(config.maintenance){
  if(!admin)throw Error('Native runtime maintenance requires explicitly configured native administration');
  const maintenance=createMaintenanceCapabilities({nativeAdmin:admin.perform,inspectResidentRuntime:(session,args)=>host.nativeControlExisting(session,'runtime.inspect',args),onInvalidate:invalidate,authorize:async context=>{if(context.account!==config.account)throw Error('Maintenance account mismatch');await authorizeMaintenance?.(context);}});owners.push(maintenance);bindings.set(maintenance,bindings.get(nativeCapabilities));
 }
 if(config.applicationUpdates)owners.push(createApplicationUpdateCapabilities({supervisor:applicationUpdateSupervisor,directory:config.quiescence?join(config.stateDirectory,'capabilities','application-updates'):undefined,onMayBeIdle:mayBeIdle,onInvalidate:invalidate,authorize:async context=>{if(context.account!==config.account)throw Error('Application update account mismatch');await authorizeMaintenance?.(context);}}));
 if(config.media)owners.push(await composeMedia(config.media,ownerContext,{nativeAdmin:admin}));
 if(config.mcp){mcp=composeMCP(config.mcp,ownerContext);owners.push(mcp);ownerContext.qualifiedObservation=(...args)=>mcp.qualifiedObservation(...args);}
 if(config.notifications){notifications=await composeNotifications(config.notifications,ownerContext);owners.push(notifications);ownerContext.notifySchedule=notifications.notifySchedule;}
 if(config.operations){operations=await composeOperations(config.operations,ownerContext);owners.push(operations);}
 if(config.coordination){coordination=await composeCoordination(config.coordination,ownerContext,{host:()=>host,operations,admit});owners.push(coordination);}
 if(config.worktrees){const composed=await composeWorktrees(config.worktrees,ownerContext);owners.push(composed.owner);roots.push(composed.executionRoot);}
 if(config.publishing)owners.push(await composePublishing(config.publishing,ownerContext,authorizePublication));
 if(config.recall){recall=await composeRecall(config.recall,ownerContext);owners.push(recall);}
 if(config.feedback)owners.push(await composeFeedback(config.feedback,ownerContext,authorizeFeedback));
 if(config.portability){
  const evidenceOwners=new Map([['unified.resources',resources],...(operations?[['unified.operations',operations]]:[])]);
  const omissions=owners.filter(owner=>![...evidenceOwners.values()].includes(owner)).map(owner=>({topics:Object.keys(owner.manifest?.topics??{}),reason:'This owner has no qualified transfer evidence export; its source records remain on the source host'}));
  portability=await composePortability(config.portability,ownerContext,{engines:config.engines,roots,host:()=>host,evidenceOwners,omissions,authorizeTransfer});owners.push(portability.owner);
  if(config.host?.transferIdentity&&config.host.transferIdentity!==portability.identity)throw Error('Configured transfer identity differs from the trusted owner');
 }
 const nativeAuthority=config.recovery?.nativeAuthority??config.account+':'+config.nativeAdmin?.engine;
 if(config.recovery){recovery=composeRecovery(config.recovery,ownerContext,{admin,host:()=>host,engineId:config.nativeAdmin?.engine,nativeAuthority,authorize:authorizeRecovery});owners.push(recovery);}
 capabilities=composeCapabilities(owners,{account:config.account});
 // Transfer peers close their local intake before the shared admin owner holds
 // the one exclusive native-home writer lease.
 const quiescenceOwners=portability?[portability.owner,...owners.filter(owner=>owner!==portability.owner)]:owners;
 if(config.quiescence)quiescence=composeQuiescence(config.quiescence,quiescenceOwners,{bindings,onMayBeIdle:mayBeIdle,verifyRelease:recoveryReleaseVerifier({...config.quiescence,nativeAuthority,recovery:()=>recovery,fallback:verifyQuiescenceRelease})});
  if(config.legacyClientState){
   if(config.legacyClientState.account!==config.account)throw Error('Legacy client storage must explicitly belong to the authenticated account');
   migration=createClientMigration({...config.legacyClientState,resolveNative:catalog?params=>catalog.request('resolveNative',{...params,allowedWorkspaceRoots:roots}):undefined});
  }
  const gatewayConfig={...config.gateway,account:config.account,webDirectory:config.webDirectory,hostToken:token,authorize};
  host=await createHost({...config.host,...(quiescence?{quiescence}:{}),...(portability?{transferIdentity:portability.identity}:{}),stateDirectory:join(config.stateDirectory,'host'),engines:config.engines,allowedWorkspaceRoots:roots,defaultWorkingDirectory:workspace,host:'127.0.0.1',port:0,bearerToken:token,allowedOrigins:[],capabilities,catalog,clientMetadata:migration?.metadata,resourceProviders:[...capabilities.resources,...(migration?[migration.resourceProvider]:[])],
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
    const channel=args.sessionId===context.session?context.session:capabilities.manifest.topics[advertised.topic]?.scope==='host'?'ahp-root://':context.session;
    return host.invokeCapability({channel,topic:advertised.topic,operation:advertised.operation,version:1,args,commandId},{actorId:'agent:'+context.nativeSessionId,origin:'agent'});
   }
  });
  const handlers=owners.flatMap(owner=>owner.httpHandlers??(owner.handleMedia?[{matches:path=>path.startsWith('/media/'),handle:owner.handleMedia}]:[]));
  gateway=await createGateway({...gatewayConfig,hostUrl:host.url,handlers});
  host.config.allowedOrigins=[gateway.url];
  for(const owner of owners)await owner.initializeOrigin?.(gateway.url);
  for(const owner of owners)await owner.start?.();
  let closing;
  return {url:gateway.url,host,capabilities,resources,quiescence,close(){if(!closing){stopping=true;closing=(async()=>{await gateway.close();await workspaces?.close();await host.close();await admin?.close();await capabilities.close();migration?.close();})();}return closing;}};
 }catch(error){stopping=true;await gateway?.close();await workspaces?.close();await host?.close();if(!host)await catalog?.close();await admin?.close();await Promise.allSettled(owners.map(owner=>owner.close?.()));migration?.close();throw error;}
}
