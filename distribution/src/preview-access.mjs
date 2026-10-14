import {createServer} from 'node:https';
import {request as requestHTTP} from 'node:http';
import {randomBytes,createHash,timingSafeEqual} from 'node:crypto';

// Task-owned preview access only. This is not the product authentication owner.
export async function createPreviewAccess({origin,host,port,key,cert,accessCode,backendPort,ingressGate}) {
 if(!ingressGate||typeof ingressGate.enter!=='function'||typeof ingressGate.inspect!=='function')throw Error('counted_ingress_required');
 const authority=new URL(origin).host,cookieName='__Host-unified-ahp-preview',sessions=new Map(),peers=new Set();
 if(new URL(origin).origin!==origin||new URL(origin).protocol!=='https:'||!Number.isInteger(backendPort)||backendPort<1||backendPort>65535||typeof accessCode!=='string'||accessCode.length<40)throw Error('Private HTTPS preview credentials required');
 const expected=createHash('sha256').update(accessCode).digest();let loginAttempts=0,loginWindow=Date.now(),closing=false;
 const script=`const input=document.querySelector('input');const fragment=location.hash.slice(1);history.replaceState(null,'','/preview/login');document.querySelector('form').onsubmit=async event=>{event.preventDefault();const response=await fetch('/preview/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({code:input.value})});input.value='';if(response.ok)location.replace('/');else document.querySelector('p').textContent='Access code was not accepted. Try again shortly.'};if(fragment){input.value=fragment;document.querySelector('form').requestSubmit();}`;
 const html='<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Amplifier Unified preview</title></head><body><h1>Amplifier Unified preview</h1><p>Use the private access link for this isolated development preview.</p><form><label>Preview access code <input type="password" autocomplete="off" required></label><button>Open preview</button></form><script>'+script+'</script></body></html>';
 const clean=()=>{for(const [id,until] of sessions)if(until<Date.now())sessions.delete(id);};
 function authorityOK(req){return req.headers.host===authority&&(!req.headers.origin||req.headers.origin===origin);}
 function authorized(req){clean();const cookies=String(req.headers.cookie??'').split(';').map(value=>value.trim());const item=cookies.find(value=>value.startsWith(cookieName+'='));return item&&sessions.has(item.slice(cookieName.length+1));}
 function headers(req){const h={...req.headers,host:authority};for(const name of ['cookie','authorization','proxy-authorization','x-forwarded-for','x-forwarded-host','x-forwarded-proto'])delete h[name];return h;}
 function refuse(res,status=403){res.writeHead(status,{'Cache-Control':'no-store','Content-Type':'text/plain','Referrer-Policy':'no-referrer'});res.end('Preview access required');}
 const server=createServer({key,cert},(req,res)=>{
  const done=ingressGate.enter();if(!done)return refuse(res,503);
  let frontClosed=false,backendClosed=true;
  const settle=()=>{if(frontClosed&&backendClosed)done();};
  res.once('close',()=>{frontClosed=true;settle();});
  void(async()=>{
  res.setHeader('Cache-Control','no-store');res.setHeader('Referrer-Policy','no-referrer');
  if(closing||!authorityOK(req))return refuse(res);
  if(req.url==='/preview/login'&&req.method==='GET'){
   res.writeHead(200,{'Content-Type':'text/html; charset=utf-8','Content-Security-Policy':"default-src 'none'; script-src 'sha256-"+createHash('sha256').update(script).digest('base64')+"'; connect-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"});return res.end(html);
  }
  if(req.url==='/preview/login'&&req.method==='POST'){
   if(req.headers.origin!==origin||req.headers['content-type']!=='application/json')return refuse(res);
   if(Date.now()-loginWindow>60_000){loginWindow=Date.now();loginAttempts=0;}
   if(++loginAttempts>30)return refuse(res,429);
   const chunks=[];let size=0;for await(const chunk of req){size+=chunk.length;if(size>2048){refuse(res,413);req.destroy();return;}chunks.push(chunk);}
   let code;try{code=JSON.parse(Buffer.concat(chunks).toString()).code;}catch{return refuse(res);}
   if(typeof code!=='string'||!timingSafeEqual(createHash('sha256').update(code).digest(),expected))return refuse(res);
   clean();if(sessions.size>=64)return refuse(res,429);const id=randomBytes(32).toString('base64url');sessions.set(id,Date.now()+12*60*60*1000);
   res.writeHead(204,{'Set-Cookie':cookieName+'='+id+'; HttpOnly; Secure; SameSite=Strict; Path=/; Max-Age=43200'});return res.end();
  }
  if(!authorized(req)){
   if(req.method==='GET'&&req.url==='/'){res.writeHead(303,{Location:'/preview/login'});return res.end();}
   return refuse(res);
  }
  if(frontClosed)return;
  backendClosed=false;
  const upstream=requestHTTP({host:'127.0.0.1',port:backendPort,path:req.url,method:req.method,headers:headers(req)},response=>{res.writeHead(response.statusCode,{...response.headers,'cache-control':'no-store','referrer-policy':'no-referrer'});response.pipe(res);});
  upstream.once('close',()=>{backendClosed=true;settle();});
  upstream.setTimeout(120_000,()=>upstream.destroy());upstream.on('error',()=>{if(!res.headersSent)res.writeHead(502);res.end('Preview is unavailable');});req.on('aborted',()=>upstream.destroy());res.on('close',()=>upstream.destroy());req.pipe(upstream);
 })().catch(()=>{if(!res.headersSent)refuse(res);else res.destroy();});});
 server.requestTimeout=30_000;server.headersTimeout=15_000;
 server.on('upgrade',(req,socket,head)=>{
  if(closing||!authorityOK(req)||req.headers.origin!==origin||!authorized(req)||req.url!=='/ahp'||peers.size>=128){socket.end('HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n');return;}
  const done=ingressGate.enter();if(!done){socket.end('HTTP/1.1 503 Service Unavailable\r\nConnection: close\r\n\r\n');return;}
  // Count both front and upstream lifetimes, including pending handshakes.
  // Count and own the front socket before awaiting the upstream handshake. An
  // upgraded socket is no longer covered by server.closeAllConnections().
  const upstream=requestHTTP({host:'127.0.0.1',port:backendPort,path:req.url,method:'GET',headers:headers(req)});
  const pair={front:socket,back:undefined,closed:false,stop:undefined,frontClosed:false,backClosed:false,requestClosed:false};
  const settle=()=>{if(pair.frontClosed&&(pair.back?pair.backClosed:pair.requestClosed)){peers.delete(pair);done();}};
  const stop=()=>{if(pair.closed)return;pair.closed=true;upstream.destroy();socket.destroy();pair.back?.destroy();};pair.stop=stop;peers.add(pair);
  socket.on('close',()=>{pair.frontClosed=true;stop();settle();});socket.on('error',stop);upstream.on('error',stop);
  upstream.once('close',()=>{pair.requestClosed=true;if(!pair.back)stop();settle();});
  upstream.setTimeout(15_000,stop);
  upstream.on('response',response=>{response.destroy();stop();});
  upstream.on('upgrade',(response,back,rest)=>{
   // Own every upgraded backend before checking shutdown. A front close can
   // race this callback; request closure alone must not settle its socket.
   pair.back=back;back.on('close',()=>{pair.backClosed=true;stop();settle();});back.on('error',stop);
   if(closing||pair.closed){back.destroy();stop();return;}
   upstream.setTimeout(0);
   socket.write('HTTP/1.1 101 Switching Protocols\r\n'+Object.entries(response.headers).map(([name,value])=>name+': '+value).join('\r\n')+'\r\n\r\n');if(rest.length)socket.write(rest);if(head.length)back.write(head);socket.pipe(back);back.pipe(socket);
  });upstream.end();
 });
 await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(port,host,resolve);});
 return {server,close:()=>{if(ingressGate.inspect().active)throw Error('manual_ingress_active');return new Promise(resolve=>{closing=true;for(const pair of peers)pair.stop();sessions.clear();server.close(resolve);server.closeAllConnections();});}};
}
