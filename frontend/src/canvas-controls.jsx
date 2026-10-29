import React,{createContext,useContext,useRef,useState} from 'react';
import {createPortal} from 'react-dom';
import {hasLocalCanvasEdits} from './canvas-reload-guard.js';

// Controls share one stable surface. Pinning changes its layout, never the viewers.
export const CanvasControlsHost=createContext(undefined);
// Primary common actions have their own stable compact row. Inline viewers keep
// their existing controls: absence of this context is not a second toolbar.
export const CanvasPrimaryActionsHost=createContext(undefined);
// Refresh is a saved-state read, not a renderer reload or a selection command.
// A same-version check must retain live HTML and native-renderer input.
export function CanvasDocumentRefresh({view,dispatch}){
 const [check,setCheck]=useState(null),pending=useRef(false),[load,setLoad]=useState(null),loading=useRef(false);
 const selection=JSON.stringify([view.resourceId,view.selectedVersion]);
 const versionOf=value=>JSON.stringify([value.resourceRevision,value.latestVersion]);
 const current=useRef(selection);current.current=selection;
 const binding=JSON.stringify([view.resourceId,view.resourceRevision,view.generation]);
 const shown=check?.selection===selection?check:null;
 const historical=view.selectedVersion!=null&&view.selectedVersion<view.latestVersion;
 const identity=view.previewRetained?`Viewing v${view.runningVersion}`:view.selectedVersion==null?`Latest · Version ${view.runningVersion??view.latestVersion}`:`Version ${view.selectedVersion} · Saved`;
 const freshness=view.previewRetained?`v${view.latestVersion} available · preview retained`:historical?`Historical · Version ${view.latestVersion} available`:'Current saved content';
 const loadVersion=async()=>{
  if(loading.current||load?.selection===selection&&load?.unconfirmed)return;
  if(hasLocalCanvasEdits()){setLoad({selection,error:'Save or cancel unfinished Canvas edits before loading another version.'});return}
  const target=Object.fromEntries(['viewId','resourceId','resourceRevision','generation'].map(key=>[key,view[key]]));
  loading.current=true;setLoad({selection,binding,pending:true});
  try{
   const result=await dispatch('canvas.views.loadVersion',{...target,version:view.latestVersion});
   if(!result?.accepted)throw Error('The version load was not accepted.');
   if(current.current===selection)setLoad(null);
  }catch(error){if(current.current===selection)setLoad({selection,binding,error:error.message,unconfirmed:!!error.unconfirmed})}
  finally{loading.current=false}
 };
 const shownLoad=load?.selection===selection&&(load.pending||load.unconfirmed||load.binding===binding)?load:null;
 const refresh=async()=>{
  if(pending.current)return;
  pending.current=true;setCheck({selection,pending:true});
  try{
   const receipt=await dispatch('canvas.views.inspect',{});
   const observed=receipt?.result?.views?.find(item=>item.viewId===view.viewId);
   if(!observed||observed.resourceId!==view.resourceId||observed.selectedVersion!==view.selectedVersion)throw Error('The selected document changed. Check the document now open.');
   if(current.current===selection){setCheck({selection,checkedVersion:versionOf(observed)});setLoad(value=>value?.unconfirmed?null:value)}
  }catch(error){if(current.current===selection)setCheck({selection,error:error.message})}
  finally{pending.current=false}
 };
 return <>
  <button type="button" className="a-soft a-canvas-refresh" aria-label="Refresh document" title="Check saved content and versions without reloading the preview or reading the source file" aria-disabled={!!shown?.pending} onClick={refresh}>Refresh</button>
  <div className="a-canvas-freshness" role={shown?.error?'alert':'status'} aria-live="polite">
   {shown?.pending?'Checking saved content…':shown?.error?`Could not refresh: ${shown.error} The open document is retained.`:`${shown?.checkedVersion===versionOf(view)?'Checked · ':''}${identity} · ${freshness}`}
   {view.previewRetained&&<button type="button" className="a-soft a-canvas-load-version" aria-disabled={!!shownLoad?.pending||!!shownLoad?.unconfirmed} onClick={loadVersion}>{shownLoad?.pending?'Loading saved version…':`Load v${view.latestVersion} — resets preview and runs its code`}</button>}
   {shownLoad?.error&&<p role="alert">{shownLoad.unconfirmed?'Version load completion is unconfirmed. Refresh to check the current version before another Load.':`${shownLoad.error} The current preview is retained.`}</p>}
  </div>
 </>;
}
export const usePrimaryCanvas=()=>useContext(CanvasPrimaryActionsHost)!==undefined;
export function CanvasPrimaryAction({children}){
 const host=useContext(CanvasPrimaryActionsHost);
 return host===undefined?<CanvasControl>{children}</CanvasControl>:host?createPortal(children,host):null;
}
export function CanvasControl({children,inline=false}){
 const host=useContext(CanvasControlsHost);
 return inline||host===undefined?children:host?createPortal(children,host):null;
}
export function CanvasViewControls({children,label}){
 const host=useContext(CanvasControlsHost),[target,setTarget]=useState(null);
 if(host===undefined)return children;
 return <><CanvasControl><div ref={setTarget} className="a-canvas-view-tools" role="group" aria-label={label}/></CanvasControl><CanvasControlsHost.Provider value={target}>{children}</CanvasControlsHost.Provider></>;
}
