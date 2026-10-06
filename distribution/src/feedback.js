import {configuredOwnerLaunch} from './owner-launch.js';
import {mkdir,writeFile,rename} from 'node:fs/promises';
import {join} from 'node:path';
import {randomUUID,createHash} from 'node:crypto';
import {createResourcesCapability} from '@amplifier/unified-resources-capability';
import {createFeedbackCapability,feedbackUploadScope} from '@amplifier/unified-feedback-capability';

/** The installed default asks in the originating conversation. It grants one
 * exact effect only; no model claim or caller argument can approve itself. */
export function feedbackPublicationAuthorization(context){
 if(typeof context.confirmCapability!=='function')return undefined;
 return async({operation,args,context:caller})=>{
  const session=typeof caller.session==='string'?caller.session:caller.session?.uri;
  if(caller.account!==context.account||caller.origin!=='agent'||!session)throw Object.assign(Error('Feedback approval requires its authenticated originating conversation'),{data:{executed:false}});
  const titles={'feedback.submit':'Send this feedback report?','feedback.comment':'Post this feedback comment?','feedback.update':'Update this feedback report?','feedback.close':'Close this feedback report?','feedback.reopen':'Reopen this feedback report?','feedback.excerpt.stage':'Share this conversation excerpt with feedback?'};
  if(!titles[operation])throw Error('Unsupported feedback publication review');
  await context.confirmCapability(session,{operation,title:titles[operation],args});
 };
}

export async function composeFeedback(config,context,authorizeFeedback){
 const directory=join(context.directory,'feedback');await mkdir(directory,{recursive:true,mode:0o700});
 let owner=config.owner;
 if(!owner){
  if(!config.python&&!config.command)throw Error('Feedback requires an independently installed owner executable');
  const path=join(directory,'launch.json'),temporary=path+'.'+randomUUID();await writeFile(temporary,JSON.stringify({dataDir:directory}),{mode:0o600});await rename(temporary,path);
  owner=configuredOwnerLaunch(config,'amplifier_unified_feedback.server',path,{cwd:directory});
 }
 const uploads=createResourcesCapability({directory:join(directory,'uploads'),onMayBeIdle:context.onMayBeIdle,inspectSession:async session=>{
  if(session!==feedbackUploadScope)throw Error('Unknown feedback upload partition');return {session};
 }});
 return createFeedbackCapability({...context,owner,uploadOwner:uploads,authorizeFeedback:authorizeFeedback??feedbackPublicationAuthorization(context),readExport:async({session,uri})=>{
  const metadata=await context.inspectExportResource(uri,session),size=metadata.summary?.bytes;
  if(!Number.isSafeInteger(size)||size<1||size>64000||metadata.mimeType!=='text/markdown'||metadata.summary?.minimal!==true||metadata.summary?.format!=='markdown'||!/^[a-f0-9]{64}$/.test(metadata.contentHash))throw Error('Choose a current minimal Markdown snapshot up to 64 KB');
  const url=new URL(uri);url.searchParams.set('offset','0');url.searchParams.set('limit','64000');
  const page=await context.readExportResource(url.href),bytes=Buffer.from(page.data??'','base64');
  if(page.encoding!=='base64'||page.offset!==0||page.nextOffset!==null||page.totalBytes!==size||bytes.length!==size||page.contentHash!==metadata.contentHash||createHash('sha256').update(bytes).digest('hex')!==metadata.contentHash)throw Error('Immutable feedback excerpt changed');
  return {...metadata,text:new TextDecoder('utf-8',{fatal:true}).decode(bytes)};
 }});
}
