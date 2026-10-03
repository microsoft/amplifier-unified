import {createServer} from 'node:http';
import {createReadStream} from 'node:fs';
import {readFile,realpath,stat} from 'node:fs/promises';
import {join,relative,isAbsolute,extname} from 'node:path';
import {createHash} from 'node:crypto';
import {WebSocket,WebSocketServer} from 'ws';

const mime={'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.css':'text/css; charset=utf-8','.json':'application/json','.webmanifest':'application/manifest+json','.svg':'image/svg+xml','.png':'image/png','.ico':'image/x-icon','.woff2':'font/woff2','.wasm':'application/wasm'};
const local=new Set(['127.0.0.1','::1','localhost']);
const encode=value=>JSON.stringify(value).replaceAll('<','\\u003c').replaceAll('\u2028','\\u2028').replaceAll('\u2029','\\u2029');
/** One user/account per host. Remote gateways must supply an authentication owner. */
export async function createGateway({hostUrl,hostToken,webDirectory,host='127.0.0.1',port=0,origin,account,authorize,maxConnections=128,handlers=[]}){
 if(typeof account!=='string'||!account||account.length>512)throw Error('A stable non-secret account identity is required');
 if(!local.has(host)&&!authorize)throw Error('A non-loopback gateway requires an authentication owner');
 const root=await realpath(webDirectory),peers=new Set();let publicOrigin=origin,closing=false;
 const wss=new WebSocketServer({noServer:true,maxPayload:16*1024*1024,perMessageDeflate:false});
 async function admit(request){
  if(closing||!publicOrigin||request.headers.host!==new URL(publicOrigin).host)throw Error('Unexpected gateway authority');
  if(request.headers.origin&&request.headers.origin!==publicOrigin)throw Error('Unexpected browser origin');
  if(authorize){const identity=await authorize(request);if(!identity||identity.account!==account)throw Error('Account authorization required');}
 }
 const server=createServer((req,res)=>{void (async()=>{
  await admit(req);const url=new URL(req.url,publicOrigin);
  for(const handler of handlers)if(handler.matches(url.pathname)){await handler.handle(req,res,{account,origin:publicOrigin});return;}
  if(!['GET','HEAD'].includes(req.method)){res.writeHead(405,{Allow:'GET, HEAD'});return res.end();}
  let bytes,type,path;
  if(url.pathname==='/connection.js'){bytes=Buffer.from('window.__AMPLIFIER_ACCOUNT__='+encode(account)+';\n');type=mime['.js'];}
  else if(url.pathname==='/health'){bytes=Buffer.from(JSON.stringify({status:'ready',protocol:'0.9.0'}));type=mime['.json'];}
  else{
   const requested=decodeURIComponent(url.pathname);if(requested.includes('\0')||requested.includes('\\'))throw Error('Invalid asset path');
   try{path=await realpath(join(root,requested==='/'?'index.html':requested));}catch(error){if(error.code==='ENOENT'){res.writeHead(404);return res.end('Not found');}throw error;}
   const rel=relative(root,path);if(rel==='..'||rel.startsWith('../')||isAbsolute(rel))throw Error('Asset lies outside client package');const info=await stat(path);if(!info.isFile()){res.writeHead(404);return res.end('Not found');}
   type=mime[extname(path)]??'application/octet-stream';
   if(extname(path)==='.html'){const html=await readFile(path,'utf8');bytes=Buffer.from(html.replace(/<head([^>]*)>/i,'<head$1><script src="/connection.js"></script>'));}
   else{const tag='"'+createHash('sha256').update(String(info.size)+':'+String(info.mtimeMs)).digest('hex').slice(0,24)+'"';res.setHeader('ETag',tag);res.setHeader('Cache-Control',url.pathname.startsWith('/assets/')?'private, max-age=31536000, immutable':'private, max-age=0, must-revalidate');if(req.headers['if-none-match']===tag){res.writeHead(304);return res.end();}res.setHeader('Content-Length',info.size);}
  }
  res.setHeader('Content-Type',type);res.setHeader('X-Content-Type-Options','nosniff');res.setHeader('Referrer-Policy','same-origin');res.setHeader('Cross-Origin-Resource-Policy','same-origin');
  if(bytes){res.setHeader('Cache-Control','no-store');res.setHeader('Content-Length',bytes.length);}
  if(req.method==='HEAD')return res.end();if(bytes)return res.end(bytes);createReadStream(path).on('error',()=>res.destroy()).pipe(res);
 })().catch(()=>{if(!res.headersSent)res.writeHead(403,{'Cache-Control':'no-store'});res.end('Request refused');});});
 server.on('upgrade',(req,socket,head)=>{void(async()=>{
  await admit(req);if(new URL(req.url,publicOrigin).pathname!=='/ahp'||peers.size>=maxConnections)throw Error('Connection unavailable');
  wss.handleUpgrade(req,socket,head,frontend=>{
   const backend=new WebSocket(hostUrl,{headers:{Authorization:'Bearer '+hostToken,Origin:publicOrigin},perMessageDeflate:false,maxPayload:16*1024*1024});const pair={frontend,backend};peers.add(pair);let ready=false,queued=[],queueBytes=0;
   const stop=()=>{if(!peers.delete(pair))return;frontend.terminate();backend.terminate();};
   frontend.on('error',stop);backend.on('error',stop);frontend.on('close',stop);backend.on('close',stop);
   backend.on('open',()=>{ready=true;for(const item of queued)backend.send(item,{binary:false});queued=[];queueBytes=0;});
   frontend.on('message',(data,binary)=>{if(binary)return stop();if(!ready){queueBytes+=data.length;if(queueBytes>1024*1024||queued.length>=32)return stop();queued.push(data);return;}if(backend.bufferedAmount+data.length>16*1024*1024)return stop();backend.send(data,{binary:false});});
   backend.on('message',(data,binary)=>{if(binary||frontend.bufferedAmount+data.length>16*1024*1024)return stop();frontend.send(data,{binary:false});});
  });
 })().catch(()=>{socket.end('HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n');});});
 await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(port,host,()=>{server.off('error',reject);resolve();});});
 const address=server.address();publicOrigin??=`http://${host.includes(':')?'['+host+']':host}:${address.port}`;
 return {url:publicOrigin,server,async close(){if(closing)return;closing=true;for(const peer of peers){peer.frontend.terminate();peer.backend.terminate();}await new Promise(resolve=>wss.close(resolve));server.closeAllConnections();await new Promise(resolve=>server.close(resolve));}};
}
