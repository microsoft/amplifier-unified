import {createServer,request} from 'node:http';

/** Owned loopback test ingress. Every HTTP response and complete upgraded socket
 * is counted before forwarding begins. This has no queued business state. */
export async function createCountedIngress(gate) {
 let target,origin,closed=false;
 const peers=new Set();
 const server=createServer((req,res)=>{
  if(closed||req.headers.host!==new URL(origin).host||req.headers.origin&&req.headers.origin!==origin){res.writeHead(403);res.end();return;}
  const done=gate.enter();if(!done){res.writeHead(503);res.end();return;}
  let frontClosed=false,backClosed=false;const settle=()=>{if(frontClosed&&backClosed)done();};
  const outgoing=request(new URL(req.url,target),{method:req.method,headers:{...req.headers,host:new URL(target).host,...(req.headers.origin?{origin:target}:{})}},reply=>{res.writeHead(reply.statusCode,reply.headers);reply.pipe(res);});
  outgoing.once('close',()=>{backClosed=true;settle();});
  res.once('close',()=>{frontClosed=true;outgoing.destroy();settle();});outgoing.on('error',()=>{if(!res.headersSent)res.writeHead(502);res.end();});
  req.pipe(outgoing);
 });
 server.on('upgrade',(req,socket,head)=>{
  if(closed||req.url!=='/ahp'||req.headers.host!==new URL(origin).host||req.headers.origin!==origin){socket.end('HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n');return;}
  const done=gate.enter();if(!done){socket.end('HTTP/1.1 503 Service Unavailable\r\nConnection: close\r\n\r\n');return;}
  const row={front:socket,back:null,request:null,frontClosed:false,backClosed:false};peers.add(row);
  const settle=()=>{if(row.frontClosed&&row.backClosed){peers.delete(row);done();}};
  const stop=()=>{socket.destroy();row.back?.destroy();row.request?.destroy();};
  socket.on('error',stop);socket.once('close',()=>{row.frontClosed=true;row.back?.destroy();row.request?.destroy();settle();});
  const outgoing=request(new URL(req.url,target),{headers:{...req.headers,host:new URL(target).host,origin:target}});
  row.request=outgoing;
  outgoing.on('error',stop);
  outgoing.once('close',()=>{if(!row.back){row.backClosed=true;socket.destroy();settle();}});
  outgoing.once('response',reply=>{reply.resume();stop();});
  outgoing.once('upgrade',(reply,backend,backendHead)=>{
   row.back=backend;row.request=null;
   backend.on('error',stop);backend.once('close',()=>{row.backClosed=true;socket.destroy();settle();});
   socket.write('HTTP/1.1 101 Switching Protocols\r\n'+Object.entries(reply.headers).map(([key,value])=>key+': '+value).join('\r\n')+'\r\n\r\n');
   if(head.length)backend.write(head);if(backendHead.length)socket.write(backendHead);
   socket.pipe(backend);backend.pipe(socket);
  });outgoing.end();
 });
 await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(0,'127.0.0.1',resolve);});
 origin='http://127.0.0.1:'+server.address().port;
 return {url:origin,forward(url){target=url;},async drain(){for(let n=0;n<200&&gate.inspect().active;n++)await new Promise(r=>setTimeout(r,10));if(gate.inspect().active)throw Error('Ingress did not drain');},
  async close(){closed=true;for(const row of peers){row.front.destroy();row.back?.destroy();row.request?.destroy();}server.closeAllConnections();await new Promise(resolve=>server.close(resolve));for(let n=0;n<200&&peers.size;n++)await new Promise(r=>setTimeout(r,10));if(peers.size)throw Error('Ingress peers did not close');}};
}
