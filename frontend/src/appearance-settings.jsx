import React,{useContext,useEffect,useRef,useState} from 'react';
import {Check,Download,Upload,Palette,Sun,Moon,Monitor,FolderOpen,RefreshCw} from 'lucide-react';
import {SettingsLayoutContext} from './settings-layout';
import {FileDrop} from './settings-ui';
import {ThemeDecorationControl} from './theme-presentation';
import './appearance-settings.css';

const fallback={bg:'#e9ecf5',surface:'#ffffff',ink:'#23304d',accent:'#6353c7',line:'#d7ddec'};
function Swatch({item}){
 const p={...fallback,...item.palette};
 return <div className="a-appearance-swatch" aria-hidden="true" style={{'--sw-bg':p.bg,'--sw-surface':p.surface,'--sw-ink':p.ink,'--sw-accent':p.accent,'--sw-line':p.line,'--sw-radius':item.radius||'12px',backgroundImage:item.background||'none',fontFamily:item.heading||'inherit',backgroundSize:item.id==='builtin:graphite'?'16px 16px':'cover'}}>
  <div className="a-swatch-header"><span>◉ Amplifier</span><span>•••</span></div>
  <div className="a-swatch-body"><div className="a-swatch-nav"><i/><i/><i/></div><div className="a-swatch-chat"><strong>Room for ideas.</strong><span className="a-swatch-line"/><span className="a-swatch-line short"/><div className="a-swatch-prompt"><span>Ask anything…</span><b>↑</b></div></div></div>
 </div>;
}

export function AppearanceSettings({state,shell,act,name,css,preview,setName,setCss,setPreview,defaultSkin,simple=false,advancedOnly=false,onAdvanced}){
 const {active}=useContext(SettingsLayoutContext);
 const [libraryError,setLibraryError]=useState('');
 const [library,setLibrary]=useState({items:[],errors:[],directory:''}),[selected,setSelected]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState(''),[advanced,setAdvanced]=useState(false);
 const api=useRef(act),alive=useRef(true),generation=useRef(0),previewing=useRef(preview);api.current=act;previewing.current=preview;
 const presentation=shell.composition.presentation,scheme=presentation.scheme||state.view?.scheme||'system';
 async function refresh(){const receipt=await api.current('theme.list',{});if(!Array.isArray(receipt.result?.items))throw Error('The appearance library is unavailable. Try reopening Settings.');if(alive.current){setLibrary(receipt.result);setLibraryError('')}}
 useEffect(()=>{if(!active)return;alive.current=true;refresh().catch(e=>setLibraryError(e.message));const timer=setInterval(()=>refresh().catch(()=>{}),5000);return()=>{alive.current=false;generation.current++;clearInterval(timer);if(previewing.current){setPreview(false);api.current('view.update',{patch:{themePreview:false}}).catch(()=>{})}}},[active]);
 async function run(fn){setBusy(true);setError('');setNotice('');try{await fn()}catch(e){if(alive.current)setError(e.message)}finally{if(alive.current)setBusy(false)}}
 async function choose(item){
  const current=++generation.current;
  await run(async()=>{const {result}=await api.current('theme.read',{id:item.id});if(!alive.current||current!==generation.current)return;setSelected(item.id);setName(result.name);setCss(result.css);const receipt=await api.current(simple?'theme.apply':'theme.preview',{name:result.name,css:result.css});if(receipt?.accepted===false)throw Error(receipt.error||'Could not save appearance.');setPreview(!simple);if(simple)setNotice(result.name+' saved.')});
 }
 async function apply(){await run(async()=>{await api.current('theme.apply',{name,css});setPreview(false);setNotice(name+' applied.')})}
 async function revert(){await run(async()=>{if(preview)await api.current('theme.revert',{});setPreview(false);setName(state.theme?.name||'Amplifier Unified');setCss(state.theme?.css||defaultSkin);setSelected(null)})}
 async function changeDraft(patch){
  if('name' in patch)setName(patch.name);if('css' in patch)setCss(patch.css);setSelected(null);setPreview(false);
  await api.current('view.update',{patch:{themeDraft:patch.css??css,themeDraftName:patch.name??name,themePreview:false}});
 }
 async function save(){await run(async()=>{const {result}=await api.current('theme.save',{name,css});setSelected(result.id);await refresh();setNotice('Saved to your appearance library.')})}
 async function upload(file){
  if(!file)return;if(file.size>990000)throw Error('Choose a CSS appearance smaller than 1 MB.');
  const imported=await file.text(),title=file.name.replace(/\.amplifier\.css$|\.css$/i,'').slice(0,100);
  const {result}=await api.current('theme.save',{name:title,css:imported});await refresh();setNotice(title+' added to your library. Select it to preview.');return result;
 }
 const chosenId=selected||(state.theme?.name==='Amplifier Unified'?'builtin:default':library.items.find(item=>item.name===state.theme?.name)?.id);
 const setPresentation=patch=>run(()=>shell.setPresentation(patch));
 return <div className="a-appearance-settings" data-appearance-simple={simple} data-appearance-advanced={advancedOnly}>
  <div className="a-appearance-intro"><div>{simple?<p>Choose a look. Your choice is saved automatically.</p>:<><p>Make this space yours.</p><small>Choose an appearance to try it. Apply when it feels right.</small></>}</div>{!simple&&<span className="a-appearance-current"><Palette size={14}/>{preview?'Previewing '+name:state.theme?.name||'Amplifier'}</span>}</div>
  {simple&&<h4 className="a-appearance-label">Look & feel</h4>}
  <div className="a-appearance-gallery" aria-label="Appearance library">
   {library.items.map(item=><button type="button" key={item.id} className="a-appearance-card" aria-label={(simple?'Use ':'Preview ')+item.name} aria-pressed={chosenId===item.id} disabled={busy} data-action={simple?"theme.apply":"theme.preview"} onClick={()=>choose(item)}>
    <Swatch item={item}/><div className="a-appearance-card-caption"><div><strong>{item.name}</strong>{!simple&&<small>{item.tag}</small>}</div>{chosenId===item.id?<Check/>:<Palette/>}</div>{!simple&&<p>{item.description}</p>}
   </button>)}
   {!library.items.length&&!libraryError&&<p role="status">Loading appearances…</p>}
  </div>
  {preview&&<div className="a-appearance-preview" role="status"><span>{preview?'Previewing '+name:'Current appearance'}</span><div>{preview&&<button type="button" className="a-soft" disabled={busy} onClick={revert}>Cancel preview</button>}<button type="button" className="a-primary" disabled={busy||!preview} data-action="theme.apply" onClick={apply}><Check/>{simple?'Apply appearance':'Use '+name}</button></div></div>}
  {(error||libraryError)&&<p role="alert" className="a-danger">{error||libraryError}</p>}{notice&&<p role="status" className="a-appearance-notice"><Check size={15}/>{notice}</p>}
  <section className="a-appearance-options">{simple?<div className="a-appearance-color"><label htmlFor="appearance-color-mode">Color mode</label><select id="appearance-color-mode" value={scheme} disabled={busy} data-action="shell.changes.apply" onChange={event=>setPresentation({scheme:event.target.value})}><option value="system">Device</option><option value="light">Light</option><option value="dark">Dark</option></select></div>:<div className="a-appearance-option"><div><h3>Color mode</h3><p>Keep a favorite look throughout the day.</p></div><div className="a-appearance-modes" role="group" aria-label="Color mode">{[['light','Light',Sun],['dark','Dark',Moon],['system','Device',Monitor]].map(([value,label,Icon])=><button type="button" key={value} disabled={busy} aria-pressed={scheme===value} data-action="shell.changes.apply" onClick={()=>setPresentation({scheme:value})}><Icon/>{label}</button>)}</div></div>}
   <fieldset className="a-detail-level"><legend>How much would you like to see?</legend><p>Choose a calmer workspace or keep more information at hand. You can always open details.</p><div className="a-detail-choices">{[['minimal','Focused','Quiet lists and simple work summaries. Extra information stays in details.'],['standard','Balanced','Useful context and work totals, with deeper details one click away.'],['detailed','Everything','Work activity opens automatically. Counts and list actions stay visible.']].map(([value,label,description])=><label key={value} data-selected={(presentation.interfaceDetail||presentation.executionDetail||'standard')===value}><input type="radio" name="interface-detail" value={value} checked={(presentation.interfaceDetail||presentation.executionDetail||'standard')===value} disabled={busy} data-action="shell.changes.apply" onChange={()=>setPresentation({interfaceDetail:value,executionDetail:value})}/><span><strong>{label}</strong><small>{description}</small></span></label>)}</div></fieldset>
   <details className="a-everyday-disclosure" open={!simple||undefined}><summary>Layout & background</summary><div>
   <div className="a-appearance-option"><div><h3>Background</h3><p>Decorative artwork stays behind your content.</p></div><ThemeDecorationControl shell={shell} onError={setError}/></div>
   <div className="a-appearance-option"><div><label htmlFor="layout">Layout</label><p>Choose where the conversation and Canvas sit.</p></div><select id="layout" value={presentation.layout||state.view?.layout||'balanced'} disabled={busy} data-action="shell.changes.apply" onChange={e=>setPresentation({layout:e.target.value})}><option value="balanced">Canvas on the right</option><option value="conversation">More room for conversation</option><option value="work">Canvas on the left</option></select></div>

   </div></details>
  </section>
  {!simple&&<section className="a-appearance-import"><div><h3><Upload/>Your appearance library</h3><p>Upload a CSS appearance once. It will stay here across restarts and be available to every connected device.</p></div><FileDrop id="theme-import" accept=".css,text/css" action="theme.save" label="Upload appearance" hint="Drop a CSS file here · up to 1 MB" onFile={upload}/>{library.errors.map(row=><p className="a-danger" key={row.name}>{row.name}: {row.message}</p>)}<details className="a-appearance-folder"><summary><FolderOpen/>Theme folder</summary><p>CSS files placed here appear in the library automatically.</p><code>{library.directory}</code></details></section>}
  {!simple&&<details className="a-appearance-advanced" open={advanced} onToggle={e=>setAdvanced(e.currentTarget.open)}><summary>Advanced customization</summary><p>Edit or export a complete, portable CSS appearance. Embedded images and fonts travel with it.</p><label htmlFor="theme-name">Appearance name</label><input id="theme-name" value={name} data-action="view.update" onChange={e=>changeDraft({name:e.target.value}).catch(e=>setError(e.message))}/><label htmlFor="theme-css">CSS</label><textarea id="theme-css" spellCheck={false} className="a-css-editor" value={css} data-action="view.update" onChange={e=>changeDraft({css:e.target.value}).catch(e=>setError(e.message))}/><div className="a-dialog-actions"><button type="button" className="a-primary" disabled={busy} data-action="theme.save" onClick={save}>Save to library</button><button type="button" className="a-soft" disabled={busy} data-action="theme.preview" onClick={()=>run(async()=>{await api.current('theme.preview',{name,css});setPreview(true)})}>Preview</button><button type="button" className="a-soft" disabled={busy} data-action="theme.apply" onClick={apply}>Apply skin</button><button type="button" className="a-soft" disabled={busy} onClick={revert}>Revert edits</button><button type="button" className="a-soft" disabled={busy} data-action="theme.export" onClick={()=>run(()=>api.current('theme.export',{}))}><Download/>Export current appearance</button></div></details>}
  {simple&&<div className="a-everyday-footer"><button className="a-link" data-action="view.update" onClick={onAdvanced}>Custom themes & CSS</button></div>}
  {!simple&&<button type="button" className="a-link a-appearance-reset" disabled={busy} data-action="theme.reset" onClick={()=>run(async()=>{await api.current('theme.reset',{});setPreview(false);setSelected(null)})}><RefreshCw/>Restore original appearance</button>}
 </div>;
}
