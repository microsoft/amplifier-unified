import React,{createContext,useCallback,useContext,useEffect,useMemo,useRef,useState} from 'react';

const DraftContext=createContext(null);
export function useSettingsDraft(id,entry){
 const registry=useContext(DraftContext);
 useEffect(()=>{registry?.register(id,entry)});
 useEffect(()=>()=>registry?.remove(id),[registry,id]);
}

export function useSettingsDrafts(){
 const entries=useRef(new Map()),waiting=useRef(null);
 const[count,setCount]=useState(0),[prompt,setPrompt]=useState(null);
 const recount=useCallback(()=>setCount([...entries.current.values()].filter(entry=>entry.dirty).length),[]);
 const register=useCallback((id,entry)=>{const changed=!!entries.current.get(id)?.dirty!==!!entry.dirty;entries.current.set(id,entry);if(changed)recount()},[recount]);
 const remove=useCallback(id=>{if(entries.current.delete(id))recount()},[recount]);
 const context=useMemo(()=>({register,remove}),[register,remove]);
 const hasUnsaved=useCallback(()=>[...entries.current.values()].some(entry=>entry.dirty),[]);
 const confirmClose=useCallback(()=>{
  const dirty=[...entries.current.values()].filter(entry=>entry.dirty);
  if(!dirty.length)return Promise.resolve(true);
  if(waiting.current)return waiting.current.promise;
  let resolve;const promise=new Promise(done=>{resolve=done});waiting.current={promise,resolve};setPrompt(dirty);return promise;
 },[]);
 const settle=accepted=>{const pending=waiting.current;waiting.current=null;setPrompt(null);pending?.resolve(accepted)};
 useEffect(()=>{
  if(!count)return;
  const unload=event=>{event.preventDefault();event.returnValue=''};
  window.addEventListener('beforeunload',unload);return()=>window.removeEventListener('beforeunload',unload);
 },[count]);
 useEffect(()=>()=>{waiting.current?.resolve(false)},[]);
 const dialog=prompt&&<div className="a-settings-draft-overlay"><div className="a-settings-draft-confirm" role="alertdialog" aria-modal="true" aria-labelledby="settings-unsaved-title" onKeyDown={event=>{
  if(event.key==='Escape'){event.preventDefault();event.stopPropagation();settle(false)}
  if(event.key==='Tab'){const buttons=[...event.currentTarget.querySelectorAll('button')],index=buttons.indexOf(document.activeElement);event.preventDefault();buttons[(index+(event.shiftKey?-1:1)+buttons.length)%buttons.length]?.focus()}
 }}>
  <h3 id="settings-unsaved-title">Unsaved connection changes</h3>
  <p>Your changes have not been saved. Review them before closing Settings, or discard them.</p>
  <ul>{prompt.map(entry=><li key={entry.label}>{entry.label}</li>)}</ul>
  <div className="a-dialog-actions">
   <button type="button" className="a-primary" autoFocus onClick={()=>{prompt[0].review();settle(false)}}>Review and save</button>
   <button type="button" className="a-soft a-danger" onClick={()=>{prompt.forEach(entry=>entry.discard());settle(true)}}>Discard changes</button>
   <button type="button" className="a-soft" onClick={()=>settle(false)}>Keep settings open</button>
  </div>
 </div></div>;
 return {context,confirmClose,hasUnsaved,count,dialog};
}
export function SettingsDraftProvider({value,children}){return <DraftContext.Provider value={value}>{children}</DraftContext.Provider>}

// Shared view updates (including agent actions) must not unmount the only copy
// of a private field before the user chooses whether to discard it.
export function useGuardedSettingsPanel(requested,navigationRef,restore){
 const [visible,setVisible]=useState(requested),latestRestore=useRef(restore);
 latestRestore.current=restore;
 useEffect(()=>{
  if(requested===visible)return;
  const settings=panel=>panel==='settings'||panel==='appearance',navigation=navigationRef.current;
  if(!settings(visible)||settings(requested)||!navigation?.hasUnsaved()) {setVisible(requested);return;}
  let current=true;
  navigation.confirmClose().then(accepted=>{if(!current)return;if(accepted)setVisible(requested);else latestRestore.current(visible)});
  return()=>{current=false};
 },[requested,visible,navigationRef]);
 return visible;
}
