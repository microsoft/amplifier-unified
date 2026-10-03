import {join} from 'node:path';
import {setTimeout as delay} from 'node:timers/promises';
import {createPresentationCapabilities} from '@amplifier/unified-recovery-capability';

const methods=['prepareConversationPresentation','readConversationPresentationReview','applyConversationPresentation','conversationPresentationReceipt','reconcileConversationPresentation','inspectConversationPresentation','rebuildConversationPresentation','reconcileConversationPresentationRebuild'];

/** Public host port only. No native authority, foreign journal or global gate. */
export function conversationPresentationPort(host){
 const port={presentationReset:{version:1,preservesCanonical:true,preservesAuthority:true}};
 for(const method of methods)port[method]=(...args)=>{
  const current=host();
  if(typeof current?.[method]!=='function')throw Error('Configured host presentation port is unavailable');
  return current[method](...args);
 };
 return port;
}

export function presentationAuthorization(context,authorize){
 if(typeof authorize!=='function')throw Error('Conversation presentation requires explicit account authorization');
 return async(caller,operation,args)=>{
  if(caller.account!==context.account)throw Error('Presentation account mismatch');
  const identity=await authorize(caller,operation,args);
  if(identity?.accountId!==context.account)throw Error('Presentation approval must bind the authenticated account');
  return identity;
 };
}

export function composePresentation(context,{host,authorize}){
 const owner=createPresentationCapabilities({
  directory:join(context.directory,'recovery'),
  authorize:presentationAuthorization(context,authorize),
  conversationPresentation:conversationPresentationPort(host),
  onInvalidate:context.onInvalidate,onMayBeIdle:context.onMayBeIdle,
 });
 owner.resourceProvider={scheme:'amplifier-recovery',read:(params,caller)=>owner.resourceRead(params,{...caller,account:context.account})};
 return owner;
}

/** Only discovery reads are gated; exact-ID and workspace-authority operations remain available. */
export function presentationDiscoveryCatalog(catalog,host){
 if(!catalog)return catalog;
 return {
  list:params=>host().withConversationCatalogRead(()=>catalog.list(params)),
  listWorkspaces:params=>host().withConversationCatalogRead(()=>catalog.listWorkspaces(params)),
  getWorkspace:params=>catalog.getWorkspace(params),
  projectWorkspaces:params=>catalog.projectWorkspaces(params),
  workspaceProjectionStatus:params=>catalog.workspaceProjectionStatus(params),
 };
}

/** Called only by an explicit host rebuild. Root owns process scan/hint scheduling. */
export function reconstructPresentationMetadata(catalog,{timeoutMs=30000}={}){
 if(!catalog?.upsert||!catalog?.request||!catalog?.status)throw Error('Presentation reconstruction requires the configured indexed catalog');
 if(!Number.isSafeInteger(timeoutMs)||timeoutMs<1||timeoutMs>120000)throw Error('Bounded reconstruction timeout required');
 return async({readMappings})=>{
  if((await catalog.status()).phase==='running')throw Error('Catalog discovery is already running; authoritative mappings must be seeded first');
  // Seed authoritative stable identities before discovery can invent native-derived IDs.
  let cursor,count,manifestHash,seen=0;const cursors=new Set(),ids=new Set();
  do{
   const page=readMappings(cursor);
   if(!page||!Array.isArray(page.items)||page.items.length>100||!Number.isSafeInteger(page.count)||page.count<0||page.count>100000||typeof page.manifestHash!=='string'||!/^[a-f0-9]{64}$/.test(page.manifestHash)||page.nextCursor!==null&&typeof page.nextCursor!=='string')throw Error('Invalid retained presentation mapping page');
   if(count===undefined){count=page.count;manifestHash=page.manifestHash;}
   if(page.count!==count||page.manifestHash!==manifestHash)throw Error('Presentation mapping manifest changed');
   for(const record of page.items){
    if(!record||typeof record.uri!=='string'||ids.has(record.uri)||++seen>count)throw Error('Duplicate or excess presentation mapping');
    ids.add(record.uri);
    const prior=await catalog.get(record.uri);
    if(prior&&['uri','engineId','nativeSessionId','workingDirectory'].some(key=>prior[key]!==record[key]))throw Error('Indexed identity conflicts with retained presentation mapping');
    await catalog.upsert(record);
   }
   cursor=page.nextCursor;
   if(cursor){if(cursors.has(cursor))throw Error('Repeated presentation mapping cursor');cursors.add(cursor);}
  }while(cursor);
  if(seen!==count)throw Error('Incomplete retained presentation mappings');
  const previous=await catalog.status();
  if(previous.phase==='running')throw Error('Catalog discovery is already running; preserve presentation unavailability');
  await catalog.request('scan',{});
  const deadline=Date.now()+timeoutMs;
  do{
   const current=await catalog.status();
   if(current.runId!==previous.runId&&current.phase==='complete'){
    if(current.issues!==0)throw Error('Catalog metadata discovery has unresolved issues');
    return;
   }
   if(current.runId!==previous.runId&&current.phase==='failed')throw Error('Catalog metadata discovery failed');
   await delay(10);
  }while(Date.now()<deadline);
  throw Error('Catalog metadata discovery did not complete');
 };
}
