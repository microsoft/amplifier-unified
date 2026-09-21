const get=id=>document.getElementById(id);
get('host').textContent=location.host;
if(!/Macintosh|Mac OS X/.test(navigator.userAgent)){get('platform').value='linux-arm64';get('name').value='My terminal';}
let pendingId=null, pendingArgs=null;
async function action(name,args={},id=crypto.randomUUID()){
  const response=await fetch('/api/actions',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:name,args,id})});
  if(response.status===401)throw new Error('Your sign-in expired. Sign in again, then return to this page.');
  const result=await response.json().catch(()=>({error:'The service could not complete this request. Reload the page and retry.'}));
  if(!response.ok)throw new Error(result.error||'The request did not complete. Please retry.');
  return result;
}
get('prepare').addEventListener('submit',async event=>{
  event.preventDefault();
  if(get('prepare-button').disabled)return;
  const args={server:location.origin,platform:get('platform').value,name:get('name').value.trim()};
  if(JSON.stringify(args)!==JSON.stringify(pendingArgs)){pendingArgs=args;pendingId=crypto.randomUUID();}
  get('prepare-button').disabled=true;get('prepare-button').textContent='Preparing…';get('progress').hidden=false;
  get('platform').disabled=true;get('name').disabled=true;
  get('prepare').setAttribute('aria-busy','true');get('status').className='';get('status').textContent='Getting a verified client and preparing your connection…';get('ready').hidden=true;
  try{
    const {installer}=await action('terminal.prepare',args,pendingId);
    get('download').href=installer.downloadUrl;get('download').download=installer.filename;
    get('command').textContent=`/bin/bash "$HOME/Downloads/${installer.filename}"`;
    get('expiry').textContent=`Run this setup before ${new Date(installer.expiresAt*1000).toLocaleTimeString()}. It can connect one terminal installation. Download a new setup file if it expires or was already used.`;
    get('ready').hidden=false;get('status').textContent='Your setup file is ready. Download it, then finish on this computer.';
    pendingId=null;pendingArgs=null;
  }catch(error){get('status').className='error';get('status').textContent=error.message;}
  finally{get('prepare-button').disabled=false;get('platform').disabled=false;get('name').disabled=false;get('prepare-button').textContent='Prepare setup file';get('progress').hidden=true;get('prepare').setAttribute('aria-busy','false');}
});
get('copy').addEventListener('click',async()=>{
  const button=get('copy');button.disabled=true;button.textContent='Copying…';
  try{await navigator.clipboard.writeText(get('command').textContent);button.textContent='Copied';}
  catch{button.textContent='Select and copy the command above';}
  finally{button.disabled=false;}
});
async function refresh(){
  get('refresh').disabled=true;get('refresh').textContent='Refreshing…';get('connections').setAttribute('aria-busy','true');
  try{
    const {devices}=await action('terminal.devices');get('devices').replaceChildren();
    for(const device of devices){
      const item=document.createElement('li'),label=document.createElement('span'),button=document.createElement('button');
      label.textContent=device.name+' · added '+new Date(device.createdAt*1000).toLocaleDateString()+' ';
      button.type='button';button.className='secondary';button.textContent='Remove access';button.setAttribute('aria-label','Remove access for '+device.name);
      button.addEventListener('click',async()=>{
        button.disabled=true;button.textContent='Removing…';
        try{await action('terminal.revoke',{id:device.id});await refresh();}
        catch(error){get('device-status').textContent=error.message;button.disabled=false;button.textContent='Remove access';}
      });
      item.append(label,button);get('devices').append(item);
    }
    get('device-status').textContent=devices.length?'':'No terminals are connected yet. Refresh after completing setup.';
  }catch(error){get('device-status').textContent=error.message;}
  finally{get('refresh').disabled=false;get('refresh').textContent='Refresh connections';get('connections').setAttribute('aria-busy','false');}
}
get('refresh').addEventListener('click',refresh);refresh();
