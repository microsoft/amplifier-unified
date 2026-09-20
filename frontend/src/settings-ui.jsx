import {AttentionBadge,AttentionReview} from './attention';
import React,{useRef,useState,useEffect,createContext,useContext} from 'react';
import {activityRegion,beginRegionActivity} from './activity-feedback';
import {ActivityRegion,useRefreshValue} from './activity-region';
import {useOutsideDismiss} from './use-outside-dismiss';
import {CheckCircle2,Info,XCircle,LoaderCircle,ChevronRight,Upload,FolderOpen,ArrowUp,ArrowDown,GripVertical,X,ArrowLeft} from 'lucide-react';
export function ResultNotice({phase,message,detail}){
 const ref=useRef(null);
 const working=['working','queued','pending','checking','staging','validating','activating'].includes(phase),failed=['error','failed'].includes(phase),Icon=working?LoaderCircle:failed?XCircle:phase==='neutral'?Info:CheckCircle2;
 useEffect(()=>working&&message?beginRegionActivity(activityRegion(ref.current)):undefined,[working,!!message]);
 if(!message)return null;
 return <div ref={ref} className={`a-check-result ${working||phase==='neutral'?'pending':failed?'error':'success'}`} role={failed?'alert':'status'} aria-live="polite"><Icon aria-hidden="true" className={working?'a-progress-spinner':undefined}/><div><strong>{message}</strong>{detail&&<small>{detail}</small>}</div></div>;
}
export const SettingsPageContext=createContext(null);
export function SettingsGroup({id,title,summary,state,act,children}){
 const scopedPage=useContext(SettingsPageContext);
 const pages={setup:['loaded-modules','providers','routing','defaults','conversation'],capabilities:['smart-tools','add-bundles','app-bundles','loaded-modules','registries','share-bundle'],maintenance:['ready-conversations','updates','diagnostics','history','permissions','notifications','automation','repair','reset']};
 const page=scopedPage||state.view?.settingsExpanded?.find(key=>!state.view?.settingsSection||pages[state.view.settingsSection]?.includes(key)),open=page===id,heading=useRef();
 useEffect(()=>{if(open&&!scopedPage){heading.current?.focus({preventScroll:true});heading.current?.closest('.a-dialog')?.scrollTo(0,0)}},[open,scopedPage]);
 if(scopedPage)return open?<section className="a-settings-page" data-settings-page={id}><div id={'settings-group-'+id} className="a-settings-page-body">{children}</div></section>:null;
 if(page&&!open)return null;
 const navigate=()=>act('view.update',{patch:{settingsExpanded:open?[]:[id]}});
 return open?<section className="a-settings-page" key={id}><button type="button" className="a-link a-settings-back" data-action="view.update" onClick={navigate}><ArrowLeft/>Back to {({setup:'Setup',capabilities:'Capabilities',maintenance:'Maintenance'})[state.view?.settingsSection||'setup']}</button><h3 ref={heading} tabIndex={-1}>{title}</h3><AttentionReview state={state} act={act} page={id}/><div id={'settings-group-'+id} className="a-settings-page-body">{children}</div></section>:<section className="a-settings-group"><button type="button" className="a-settings-group-title" aria-label={title+(summary?' '+summary:'')} data-action="view.update" onClick={navigate}><span><strong>{title}</strong>{summary&&<small>{summary}</small>}</span><AttentionBadge state={state} page={id}/><ChevronRight aria-hidden="true"/></button></section>;
}

export function FileDrop({id,accept,disabled,action,onFile,label='Choose file',hint}){
 const input=useRef(),[over,setOver]=useState(false),[filename,setFilename]=useState(''),[error,setError]=useState(''),[busy,setBusy]=useState(false),receiving=useRef(false);
 async function receive(file){if(!file||disabled||receiving.current)return;receiving.current=true;setBusy(true);setError('');setFilename(file.name);try{await onFile(file)}catch(error){setError(error.message)}finally{receiving.current=false;setBusy(false)}}
 return <ActivityRegion busy={busy} name={id} className={`a-file-drop ${over?'drag-over':''}`} onDragOver={e=>{e.preventDefault();if(!disabled)setOver(true)}} onDragLeave={()=>setOver(false)} onDrop={e=>{e.preventDefault();setOver(false);receive(e.dataTransfer.files?.[0])}}><Upload aria-hidden="true"/><div><button type="button" className="a-soft" disabled={disabled||busy} data-operation-pending={busy||undefined} aria-busy={busy||undefined} data-action={action} onClick={()=>input.current?.click()}>{label}</button><small>{filename||hint||'Drop a file here, or browse to choose one.'}</small></div><input ref={input} id={id} className="a-file-input" type="file" accept={accept} disabled={disabled} data-action={action} aria-label={label} onChange={e=>{receive(e.target.files?.[0]);e.target.value=''}}/>{error&&<ResultNotice phase="error" message={error}/>}</ActivityRegion>;
}
export function droppedLocation(transfer){
 const text=(transfer.getData('text/uri-list')||transfer.getData('text/plain')||'').split('\n').find(line=>line.trim()&&!line.startsWith('#'))?.trim();
 if(!text)return null;
 if(text.startsWith('file://')){try{return decodeURIComponent(new URL(text).pathname)}catch{return null}}
 return text;
}
export function PathField({id,value,onChange,state,act,placeholder,action='view.update',directory=false,multiline=false}){
 const[over,setOver]=useState(false),[error,setError]=useState(''),[pathDraft,setPathDraft]=useState('');
 const picker=state.view?.locationPicker,open=picker?.controlId===id,incoming=state.locationListing?.controlId===id?state.locationListing:null;
 useEffect(()=>{if(open)setPathDraft(picker.path??incoming?.path??'')},[open,picker?.path,incoming?.path]);
 const pickerRoot=useRef(null);useOutsideDismiss(open,pickerRoot,()=>act('view.update',{patch:{locationPicker:null}}));
 const status=state.actionStatus?.['locations.list'],op=status?.target?.controlId===id?status:null;
 const busy=['working','queued'].includes(op?.phase),listing=useRefreshValue(id,incoming,busy);
 const browse=path=>{setPathDraft(path||'');setError('');act('view.update',{patch:{locationPicker:{controlId:id,path:path||''}}});return act('locations.list',{controlId:id,...(path?{path}:{}),directoriesOnly:directory})};
 const choose=path=>{onChange(multiline?[value,path].filter(Boolean).join('\n'):path);act('view.update',{patch:{locationPicker:null}})};
 const Input=multiline?'textarea':'input';
 return <div ref={pickerRoot} className={`a-path-field ${over?'drag-over':''}`} onDragOver={e=>{e.preventDefault();setOver(true)}} onDragLeave={()=>setOver(false)} onDrop={e=>{e.preventDefault();setOver(false);const path=droppedLocation(e.dataTransfer);if(path){setError('');onChange(multiline?[value,path].filter(Boolean).join('\n'):path)}else setError('Your browser hides the full path of dropped files. Use Browse, or drop a copied file or folder path.')}}><div className="a-inline-form"><Input id={id} value={value} placeholder={placeholder} data-action={action} onChange={e=>onChange(e.target.value)}/><button type="button" className="a-soft" data-action="locations.list" onClick={()=>browse(!multiline&&value?.startsWith('/')?value:undefined)}><FolderOpen/>Browse</button></div>{error&&<ResultNotice phase="error" message={error}/>} {open&&<ActivityRegion busy={busy} name={id+'-locations'} className="a-location-picker"><div className="a-settings-row"><strong>{directory?'Choose a folder':'Choose a file or folder'}</strong><button type="button" className="a-icon" aria-label="Close location picker" data-action="view.update" onClick={()=>act('view.update',{patch:{locationPicker:null}})}><X/></button></div><label htmlFor={id+'-browse-path'}>Folder path</label><div className="a-inline-form"><input id={id+'-browse-path'} value={pathDraft} placeholder="Type a path, e.g. ~/Projects" data-action="view.update" onChange={e=>{setPathDraft(e.target.value);act('view.update',{patch:{locationPicker:{...picker,path:e.target.value}}})}} onKeyDown={e=>{if(e.key==='Enter'){e.preventDefault();browse(e.currentTarget.value)}}}/><button type="button" className="a-soft" data-action="locations.list" onClick={()=>browse(pathDraft)}>Go</button></div>{op?.phase==='error'&&<ResultNotice phase="error" message={op.error}/>} {busy&&!listing&&<ResultNotice phase="working" message="Loading locations…"/>}{listing&&<><div className="a-settings-row"><button type="button" className="a-soft" data-action="locations.list" disabled={listing.parent===listing.path} onClick={()=>browse(listing.parent)}><ArrowUp/>Up</button><code className="a-wrap">{listing.path}</code></div><div className="a-location-list">{listing.entries.map(entry=><button type="button" className="a-location-entry" key={entry.path} data-action={entry.directory?'locations.list':action} onClick={()=>entry.directory?browse(entry.path):choose(entry.path)}>{entry.directory&&<FolderOpen/>}{entry.name}{entry.directory&&<ChevronRight/>}</button>)}{!listing.entries.length&&<p>No {directory?'folders':'files'} here.</p>}</div>{listing.truncated&&<p>Showing the first 300 items. Enter a more specific path above.</p>}<button type="button" className="a-primary" data-action={action} onClick={()=>choose(listing.path)}>Use this folder</button></>}</ActivityRegion>}</div>;
}
export function ReorderRow({id,group,label,index,count,onMove,disabled,children,className='',action='view.update'}){
 const[over,setOver]=useState(false);
 return <div className={`a-reorder-row ${className} ${over?'drag-over':''}`} onDragOver={e=>{if(!disabled&&e.dataTransfer.types.includes('application/x-amplifier-order')){e.preventDefault();e.dataTransfer.dropEffect='move';setOver(true)}}} onDragLeave={()=>setOver(false)} onDrop={e=>{e.preventDefault();setOver(false);try{const item=JSON.parse(e.dataTransfer.getData('application/x-amplifier-order'));if(!disabled&&item.group===group&&item.id!==id)onMove(item.id,index)}catch{}}}><button type="button" className="a-icon a-drag-handle" draggable={!disabled} disabled={disabled} data-action={action} aria-label={`Reorder ${label}. Use arrow keys to move.`} title="Drag to reorder; or use arrow keys" onDragStart={e=>{e.dataTransfer.setData('application/x-amplifier-order',JSON.stringify({group,id}));e.dataTransfer.effectAllowed='move'}} onKeyDown={e=>{if(e.key==='ArrowUp'&&index>0){e.preventDefault();onMove(id,index-1)}if(e.key==='ArrowDown'&&index<count-1){e.preventDefault();onMove(id,index+1)}}}><GripVertical/></button><div className="a-reorder-content">{children}</div><div className="a-reorder-buttons"><button type="button" className="a-icon" disabled={disabled||index===0} aria-label={`Move ${label} up`} data-action={action} onClick={()=>onMove(id,index-1)}><ArrowUp/></button><button type="button" className="a-icon" disabled={disabled||index===count-1} aria-label={`Move ${label} down`} data-action={action} onClick={()=>onMove(id,index+1)}><ArrowDown/></button></div></div>;
}

export function BundlePicker({id,value,onChange,state,act,action='view.update'}){
 const advanced=state.view?.bundleSources?.includes(id)||false;
 const setAdvanced=value=>act('view.update',{patch:{bundleSources:value?[...new Set([...(state.view?.bundleSources||[]),id])]:(state.view?.bundleSources||[]).filter(key=>key!==id)}});
 useEffect(()=>{act('bundles.list',{})},[]);
 const bundles=state.registeredBundles||[{name:'work',label:'Work',value:'work'}];
 const selected=bundles.find(row=>row.value===value);
 return <ActivityRegion busy={['queued','working'].includes(state.actionStatus?.['bundles.list']?.phase)} name={'bundles-'+id} className="a-bundle-picker"><select id={id} aria-label="Registered root bundles" value={selected?value:':custom:'} data-action={action} onChange={e=>{if(e.target.value===':custom:')setAdvanced(true);else onChange(e.target.value)}}><option value=":custom:">{value&&!selected?value:'Choose a bundle'}</option>{bundles.map(row=><option value={row.value} key={row.value}>{row.label||row.name}</option>)}</select>{selected?.description&&<p className="a-caption">{selected.description}</p>}<button type="button" className="a-link" aria-expanded={advanced} data-action="view.update" onClick={()=>setAdvanced(!advanced)}>{advanced?'Hide source':'Use a bundle source or file'}</button>{advanced&&<PathField action={action} id={id+'-source'} value={value} onChange={onChange} state={state} act={act} placeholder="Bundle name, git URL or file path"/>}</ActivityRegion>;
}
