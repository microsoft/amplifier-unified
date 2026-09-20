import React,{useState,useEffect,useRef} from 'react';
import {Layers,ChevronDown,X} from 'lucide-react';
import {BundlePicker,ResultNotice} from './settings-ui';

export const bundleLabel=(state,value)=>state.registeredBundles?.find(row=>row.value===value)?.label||value||'Bundle';
const EMPTY={};
export function BundleControl({state,session,act,working}){
 const shared=state.view?.composerBundle||EMPTY,[draft,setDraft]=useState(shared),[submitting,setSubmitting]=useState(null),submittingRef=useRef(false);
 useEffect(()=>setDraft(shared),[shared]);
 const edit=patch=>{const next={...draft,...patch};setDraft(next);act('view.update',{patch:{composerBundle:next}})};
 const current=session?.bundle||state.settings?.bundle||'work',open=draft.open&&draft.sessionId===(session?.id||null);
 const operation=session?.bundleChange,preview=session?.bundlePreview;
 const pending=!!submitting||!!session?.configurationBusy||operation?.phase==='working';
 const ready=preview?.bundle===draft.bundle&&preview?.previewId;
 const unavailable=session?.workspaceAvailable===false||session?.historyReadOnlyReason||session?.historyLoaded===false;
 const disabled=working||pending||unavailable||!draft.bundle?.trim();
 const needsModel=ready&&!preview.modelCompatible&&!draft.resetModel;
 const activeAction=submitting||operation?.action;
 const progress=activeAction==='bundle.preview'?'Previewing bundle changes…':activeAction==='bundle.fork'?'Creating a conversation with this bundle…':'Switching bundle…';
 async function run(action){
  if(submittingRef.current)return;submittingRef.current=true;setSubmitting(action);
  try{await act(action,action==='session.create'?{bundle:draft.bundle}:{sessionId:session.id,bundle:draft.bundle,...(action!=='bundle.preview'?{...(ready?{previewId:preview.previewId}:{}),resetModel:!!draft.resetModel}:{})})}
  finally{submittingRef.current=false;setSubmitting(null)}
 }
 return <div className="a-model-control a-bundle-control">
  <button type="button" className="a-model-trigger a-bundle-trigger" aria-label="Conversation bundle" aria-expanded={!!open} data-action="view.update" onClick={()=>edit({open:!open,sessionId:session?.id||null,bundle:current,resetModel:false})}><Layers/><span>{bundleLabel(state,current)}</span><ChevronDown/></button>
  {open&&<section className="a-model-popover a-bundle-popover" aria-label="Choose conversation bundle">
   <div className="a-settings-row"><strong>Conversation bundle</strong><button type="button" className="a-icon" aria-label="Close bundle settings" data-action="view.update" onClick={()=>edit({open:false})}><X/></button></div>
   <p>Choose the tools, agents, and instructions for this conversation.</p>
   <BundlePicker id="conversation-bundle" value={draft.bundle||current} onChange={bundle=>edit({bundle,resetModel:false})} state={state} act={act}/>
   {session?<>
    <p className="a-caption">Switch directly, or preview the changes first. Switching keeps history and compatible model choices, and resets previous module edits, modes, and budget overrides.</p>
    {ready&&<div className="a-bundle-changes" aria-label="Bundle changes">
     {Object.entries(preview.changes||{}).map(([kind,change])=><div key={kind}><strong>{kind[0].toUpperCase()+kind.slice(1)}</strong>{'after'in change?<p>{change.before||'None'} → {change.after||'None'}</p>:<>{change.added?.length>0&&<p>Add: {change.added.join(', ')}</p>}{change.removed?.length>0&&<p>Remove: {change.removed.join(', ')}</p>}{!change.added?.length&&!change.removed?.length&&<p>Same set; settings may change.</p>}</>}</div>)}
     <p>Instructions are replaced. Enabled app capabilities and shared settings still apply.</p>
     {preview.appCapabilities?.length>0&&<details><summary>Added app capabilities ({preview.appCapabilities.length})</summary><ul>{preview.appCapabilities.map(value=><li key={value} className="a-wrap">{value}</li>)}</ul></details>}
     {!preview.modelCompatible&&<><p>Your pinned model is unavailable in this bundle.</p><label><input type="checkbox" checked={!!draft.resetModel} data-action="view.update" onChange={e=>edit({resetModel:e.target.checked})}/> Use the new bundle’s model</label></>}
    </div>}
    <div className="a-dialog-actions">
     <button type="button" className="a-primary" disabled={disabled||needsModel||draft.bundle===current} data-action="bundle.switch" onClick={()=>run('bundle.switch')}>Switch bundle</button>
     <button type="button" className="a-soft" disabled={disabled} data-action="bundle.preview" onClick={()=>run('bundle.preview')}>Preview changes</button>
     <button type="button" className="a-soft" disabled={disabled||needsModel} data-action="bundle.fork" onClick={()=>run('bundle.fork')}>Fork with this bundle</button>
    </div>
    {working&&<p>Finish this turn and its workers to preview or switch bundles.</p>}
    {(pending||operation)&&<ResultNotice phase={pending?'working':operation.phase} message={pending?progress:operation.error||(operation.phase==='ready'?operation.action==='bundle.preview'?'Preview ready':operation.action==='bundle.fork'?'Fork created':'Bundle switched':'')}/>}
   </>:<button type="button" className="a-primary" disabled={disabled} data-action="session.create" onClick={()=>run('session.create')}>Start with this bundle</button>}
  </section>}
 </div>;
}

export function BundleDefaults({state,act}){
 const values=state.bundleDefaults||{},shared=state.view?.bundleDefaultsDraft||EMPTY;
 const [draft,setDraft]=useState(shared),[saving,setSaving]=useState(false),savingRef=useRef(false);
 useEffect(()=>setDraft(shared),[shared]);
 const scope=draft.scope||'app',value=draft.bundle??values[scope]??'';
 const edit=patch=>{const next={...draft,...patch};setDraft(next);act('view.update',{patch:{bundleDefaultsDraft:next}})};
 async function save(bundle){if(savingRef.current)return;savingRef.current=true;setSaving(true);try{await act('bundle.default',{scope,bundle,workspace:values.workspacePath||state.settings?.workspace})}finally{savingRef.current=false;setSaving(false)}}
 return <div><label htmlFor="bundle-default-scope">Default applies to</label><select id="bundle-default-scope" value={scope} data-action="view.update" onChange={e=>edit({scope:e.target.value,bundle:values[e.target.value]||''})}><option value="app">This Unified app</option><option value="workspace">This workspace on this computer</option><option value="shared">Shared Amplifier settings (including CLI)</option></select><label htmlFor="default-bundle">Default bundle for new conversations</label><BundlePicker id="default-bundle" value={value} state={state} act={act} onChange={bundle=>edit({bundle})}/><div className="a-dialog-actions"><button type="button" className="a-primary" disabled={saving||!value.trim()} data-action="bundle.default" onClick={()=>save(value.trim())}>Save default</button><button type="button" className="a-soft" disabled={saving} data-action="bundle.default" onClick={async()=>{await save(null);edit({bundle:''})}}>Use inherited default</button></div><p className="a-caption">New conversations here use <strong>{bundleLabel(state,values.effective||state.settings?.bundle)}</strong> from {({workspace:'workspace settings',app:'this Unified app',shared:'shared Amplifier settings'})[values.source]||'shared Amplifier settings'}. Workspace settings take priority over this app, then shared settings. Existing conversations keep their selected bundles.</p>{scope==='workspace'&&values.workspaceInherited&&<p className="a-caption">Clearing this computer’s override inherits the project’s {bundleLabel(state,values.workspaceInherited)} default.</p>}</div>;
}
