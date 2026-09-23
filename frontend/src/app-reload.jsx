import React,{createContext,useContext,useEffect,useRef,useState} from 'react';
import {RefreshCw} from 'lucide-react';
import {frontendBuild} from './feedback-diagnostics';
import {changedFrontend,reloadWhenSaved} from './app-reload.js';
import './app-reload.css';

export const AppReloadContext=createContext(null);
export function useAppReload({enabled,blocked,prepare}){
 const [installed,setInstalled]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const pending=useRef(false),preparation=useRef(prepare);preparation.current=prepare;
 useEffect(()=>{
  if(!enabled)return;
  let alive=true,checking=false;const controller=new AbortController();
  const check=async()=>{
   if(checking||document.hidden)return;checking=true;
   try{const response=await fetch('/build.json',{cache:'no-store',credentials:'same-origin',signal:controller.signal});if(response.ok){const value=await response.json();if(alive)setInstalled(changedFrontend(frontendBuild,value)?value:null)}}catch{}finally{checking=false}
  };
  check();const timer=setInterval(check,60000);
  window.addEventListener('focus',check);window.addEventListener('amplifier-reconnected',check);document.addEventListener('visibilitychange',check);
  return()=>{alive=false;controller.abort();clearInterval(timer);window.removeEventListener('focus',check);window.removeEventListener('amplifier-reconnected',check);document.removeEventListener('visibilitychange',check)};
 },[enabled]);
 const reload=async()=>{
  if(!installed||blocked||pending.current)return;
  pending.current=true;setBusy(true);setError('');
  try{await reloadWhenSaved({prepare:()=>preparation.current(),reload:()=>window.location.reload()})}
  catch(e){setError(e.message||'Your changes could not be saved. Try again when connected.');pending.current=false;setBusy(false)}
 };
 return {available:!!installed,version:installed?.version,busy,blocked,error,reload};
}
export function AppReloadNotice(){
 const update=useContext(AppReloadContext);if(!update?.available)return null;
 return <aside className="a-reload-notice" data-part="app-reload" aria-label="App update ready">
  <RefreshCw aria-hidden="true"/><div><strong>Update ready—reload to finish</strong><p>{update.blocked||'Load the updated app in this tab. Your chats and saved settings stay in place.'}</p>{update.error&&<p role="alert">{update.error}</p>}</div>
  <button type="button" className="a-primary" disabled={update.busy||!!update.blocked} onClick={update.reload}>{update.busy?'Saving before reload…':'Reload now'}</button>
 </aside>;
}
