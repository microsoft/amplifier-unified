import {createMCPCapabilities} from '@amplifier/unified-mcp-capabilities';
import {mkdir,writeFile,rename} from 'node:fs/promises';
import {join} from 'node:path';
import {randomUUID} from 'node:crypto';

/** MCP transport/auth stays in its installed broker; the gateway only routes. */
export function composeMCP(config,context){
 const directory=join(context.directory,'mcp'),launch=join(context.directory,'mcp-launch.json');
 const broker=config.broker??{command:config.command??config.python??'python3',args:config.command?['--config',launch]:['-I','-m','amplifier_unified_mcp.server','--config',launch],env:config.env};
 const owner=createMCPCapabilities({broker,inspectSession:context.inspectSession,registerExternal:context.registerExternal,onInvalidate:context.onInvalidate});
 owner.resourceProvider={scheme:'amplifier-mcp',read:(params,caller)=>owner.resourceRead({...params,channel:'ahp-root://'},caller)};
 owner.httpHandlers=[{matches:path=>path==='/oauth/mcp/callback',async handle(req,res,{origin}){
  if(req.method!=='GET'){res.writeHead(405,{Allow:'GET'});return res.end();}
  const url=new URL(req.url,origin);const response=await owner.oauthCallback([...url.searchParams],origin);
  res.writeHead(200,{'Content-Type':'text/plain; charset=utf-8','Cache-Control':'no-store','Referrer-Policy':'no-referrer','X-Content-Type-Options':'nosniff'});res.end(String(response));
 }}];
 owner.initializeOrigin=async origin=>{
  if(config.broker)return; // An explicitly external broker owns its launch configuration.
  await mkdir(directory,{recursive:true,mode:0o700});const temporary=launch+'.'+randomUUID();
  await writeFile(temporary,JSON.stringify({dataDir:directory,server:{port:Number(new URL(origin).port)||(origin.startsWith('https:')?443:80),public_origins:[origin]}}),{mode:0o600});await rename(temporary,launch);
 };
 return owner;
}
