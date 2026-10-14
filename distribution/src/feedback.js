import {mkdir,writeFile,rename} from 'node:fs/promises';
import {join} from 'node:path';
import {randomUUID,createHash} from 'node:crypto';
import {createResourcesCapability} from '@amplifier/unified-resources-capability';
import {createFeedbackCapability,feedbackUploadScope} from '@amplifier/unified-feedback-capability';

export async function composeFeedback(config,context,authorizeFeedback){
 const directory=join(context.directory,'feedback');await mkdir(directory,{recursive:true,mode:0o700});
 let owner=config.owner;
 if(!owner){
  if(!config.python&&!config.command)throw Error('Feedback requires an independently installed owner executable');
  const path=join(directory,'launch.json'),temporary=path+'.'+randomUUID();await writeFile(temporary,JSON.stringify({dataDir:directory}),{mode:0o600});await rename(temporary,path);
  owner={command:config.command??config.python,args:config.command?['--config',path]:['-I','-m','amplifier_unified_feedback.server','--config',path],cwd:directory,env:config.env};
 }
 const uploads=createResourcesCapability({directory:join(directory,'uploads'),onMayBeIdle:context.onMayBeIdle,inspectSession:async session=>{
  if(session!==feedbackUploadScope)throw Error('Unknown feedback upload partition');return {session};
 }});
 return createFeedbackCapability({...context,owner,uploadOwner:uploads,authorizeFeedback,readExport:async({session,uri})=>{
  const metadata=await context.inspectExportResource(uri,session),size=metadata.summary?.bytes;
  if(!Number.isSafeInteger(size)||size<1||size>64000||metadata.mimeType!=='text/markdown'||metadata.summary?.minimal!==true||metadata.summary?.format!=='markdown'||!/^[a-f0-9]{64}$/.test(metadata.contentHash))throw Error('Choose a current minimal Markdown snapshot up to 64 KB');
  const url=new URL(uri);url.searchParams.set('offset','0');url.searchParams.set('limit','64000');
  const page=await context.readExportResource(url.href),bytes=Buffer.from(page.data??'','base64');
  if(page.encoding!=='base64'||page.offset!==0||page.nextOffset!==null||page.totalBytes!==size||bytes.length!==size||page.contentHash!==metadata.contentHash||createHash('sha256').update(bytes).digest('hex')!==metadata.contentHash)throw Error('Immutable feedback excerpt changed');
  return {...metadata,text:new TextDecoder('utf-8',{fatal:true}).decode(bytes)};
 }});
}
