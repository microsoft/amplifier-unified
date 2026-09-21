const get=id=>document.getElementById(id);
const storageKey='amplifier.terminal.setup';
get('host').textContent=location.host;
if(!/Macintosh|Mac OS X/.test(navigator.userAgent)){get('platform').value='linux-arm64';get('name').value='My terminal';}
let pendingId=null, pendingArgs=null, prepared=null, refreshPromise=null;
async function action(name,args={},id=crypto.randomUUID()){
  const response=await fetch('/api/actions',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:name,args,id})});
  if(response.status===401)throw new Error('Your sign-in expired. Sign in again, then return to this page.');
  const result=await response.json().catch(()=>({error:'The service could not complete this request. Reload the page and retry.'}));
  if(!response.ok)throw new Error(result.error||'The request did not complete. Please retry.');
  return result;
}
function showPrepared(installer){
  prepared=installer;
  try{sessionStorage.setItem(storageKey,JSON.stringify(installer));}catch{}
  get('download').href=installer.downloadUrl;get('download').download=installer.filename;
  get('download').hidden=false;get('download').removeAttribute('aria-disabled');
  get('copy').disabled=false;get('copy').textContent='Copy command';
  get('command').textContent=`/bin/bash "$HOME/Downloads/${installer.filename}"`;
  get('expiry').textContent=`Run this setup before ${new Date(installer.expiresAt*1000).toLocaleTimeString()}. It can register one terminal installation. Prepare a new setup file if it expires or was already used.`;
  get('ready').hidden=false;get('status').className='';
  get('status').textContent='Your setup file is ready. Download it, then finish on this computer. This page checks registration automatically.';
  if(installer.registeredId||installer.expiresAt*1000<=Date.now()){
    get('download').hidden=true;get('download').removeAttribute('href');get('copy').disabled=true;
    get('status').textContent='Checking the saved setup registration…';
  }
}
function updateRegistration(devices){
  if(!prepared)return;
  const registered=devices.find(device=>device.setupId===prepared.id&&device.setupExpiresAt===prepared.expiresAt);
  if(registered){
    prepared.registeredId=registered.id;
    try{sessionStorage.setItem(storageKey,JSON.stringify(prepared));}catch{}
    get('status').className='success';
    get('status').textContent='Connection registered. Finish the remaining steps in Terminal and wait for “Ready,” then run amplifier-terminal in your preferred terminal app.';
    get('expiry').textContent='This setup file has been used. Registration confirms access; it does not show whether setup finished locally or the terminal is open.';
  }else if(prepared.registeredId){
    get('status').className='';get('status').textContent='This connection’s access was removed. Prepare a new setup file to reconnect.';
    get('expiry').textContent='The previous setup file has already been used.';
  }else if(prepared.expiresAt*1000<=Date.now()){
    get('status').className='';get('status').textContent='This setup file has expired. Prepare a new setup file to continue.';
    get('expiry').textContent='The previous download can no longer register a connection.';
  }else return;
  get('download').hidden=true;get('download').removeAttribute('href');get('download').setAttribute('aria-disabled','true');
  get('copy').disabled=true;
}
get('prepare').addEventListener('submit',async event=>{
  event.preventDefault();
  if(get('prepare-button').disabled)return;
  const args={server:location.origin,platform:get('platform').value,name:get('name').value.trim()};
  prepared=null;
  try{sessionStorage.removeItem(storageKey);}catch{}
  if(JSON.stringify(args)!==JSON.stringify(pendingArgs)){pendingArgs=args;pendingId=crypto.randomUUID();}
  get('prepare-button').disabled=true;get('prepare-button').textContent='Preparing…';get('progress').hidden=false;
  get('platform').disabled=true;get('name').disabled=true;
  get('prepare').setAttribute('aria-busy','true');get('status').className='';get('status').textContent='Getting a verified client and preparing your connection…';get('ready').hidden=true;
  try{
    const {installer}=await action('terminal.prepare',args,pendingId);
    showPrepared(installer);pendingId=null;pendingArgs=null;
    void refresh(false);
  }catch(error){get('status').className='error';get('status').textContent=error.message;}
  finally{get('prepare-button').disabled=false;get('platform').disabled=false;get('name').disabled=false;get('prepare-button').textContent='Prepare setup file';get('progress').hidden=true;get('prepare').setAttribute('aria-busy','false');}
});
get('copy').addEventListener('click',async()=>{
  const button=get('copy');button.disabled=true;button.textContent='Copying…';
  try{await navigator.clipboard.writeText(get('command').textContent);button.textContent='Copied';}
  catch{button.textContent='Select and copy the command above';}
  finally{button.disabled=!!get('download').hidden;}
});
function refresh(showProgress=true){
  if(refreshPromise)return refreshPromise;
  if(showProgress){get('refresh').disabled=true;get('refresh').textContent='Refreshing…';get('connections').setAttribute('aria-busy','true');}
  refreshPromise=(async()=>{
    try{
      const {devices}=await action('terminal.devices');
      const items=devices.map(device=>{
        const item=document.createElement('li'),label=document.createElement('span'),button=document.createElement('button');
        label.textContent=device.name+' · registered '+new Date(device.createdAt*1000).toLocaleDateString()+' ';
        button.type='button';button.className='secondary';button.textContent='Remove access';button.setAttribute('aria-label','Remove access for '+device.name);
        button.addEventListener('click',async()=>{
          button.disabled=true;button.textContent='Removing…';
          try{await action('terminal.revoke',{id:device.id});await refreshPromise;await refresh();}
          catch(error){get('device-status').textContent=error.message;button.disabled=false;button.textContent='Remove access';}
        });
        item.append(label,button);return item;
      });
      // Keep existing controls/focus steady during unchanged background polls.
      const signature=JSON.stringify(devices);
      if(get('devices').dataset.signature!==signature){get('devices').replaceChildren(...items);get('devices').dataset.signature=signature;}
      get('device-status').textContent=devices.length?'':'No terminal installations are registered yet. This list updates automatically during setup.';
      if(!get('prepare-button').disabled)updateRegistration(devices);
    }catch(error){get('device-status').textContent=error.message+' Existing registration details may be out of date.';}
    finally{get('refresh').disabled=false;get('refresh').textContent='Refresh connections';get('connections').setAttribute('aria-busy','false');refreshPromise=null;}
  })();
  return refreshPromise;
}
try{
  const saved=JSON.parse(sessionStorage.getItem(storageKey));
  if(saved&&saved.server===location.origin&&/^[a-f0-9]{32}$/.test(saved.id)&&saved.downloadUrl==='/api/terminal/installers/'+saved.id&&/^Amplifier-Terminal-[a-f0-9]{8}\.sh$/.test(saved.filename)&&Number.isFinite(saved.expiresAt))showPrepared(saved);
}catch{}
get('refresh').addEventListener('click',()=>refresh());
window.addEventListener('focus',()=>{if(!document.hidden)void refresh(false);});
document.addEventListener('visibilitychange',()=>{if(!document.hidden)void refresh(false);});
setInterval(()=>{if(!document.hidden)void refresh(false);},5000);
void refresh();
