// WebSockets do not occupy the browser's six HTTP/1.1 request slots. Keep each
// page's private client identity and the same snapshot/delta event contract.
export function createStateStream(path,transports,{Socket=globalThis.WebSocket,Source=globalThis.EventSource,origin=globalThis.location?.origin}={}){
 if(!transports?.includes('websocket'))return new Source(path);
 const url=new URL(path,origin);url.protocol=url.protocol==='https:'?'wss:':'ws:';url.pathname+='/ws';
 const source=new EventTarget(),socket=new Socket(url.href);let closed=false;
 source.readyState=0;
 source.close=()=>{closed=true;source.readyState=2;socket.close()};
 socket.onopen=()=>{if(closed)return;source.readyState=1;source.onopen?.()};
 socket.onmessage=message=>{
  if(closed)return;
  try{
   const frame=JSON.parse(message.data);
   if(!['state','state-delta','shell'].includes(frame.event)||!frame.data||typeof frame.data!=='object')throw Error('Invalid state frame');
   source.dispatchEvent(new MessageEvent(frame.event,{data:JSON.stringify(frame.data)}));
  }catch{socket.close(1002,'Invalid state frame')}
 };
 // Reconnection belongs to the existing app lifecycle. Never replay commands.
 socket.onclose=()=>{source.readyState=2;if(!closed)source.onerror?.()};
 socket.onerror=()=>{if(!closed)source.onerror?.()};
 return source;
}
