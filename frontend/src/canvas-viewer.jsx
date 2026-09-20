import {clientUrl} from './api';
import React,{useEffect,useId,useMemo,useRef,useState} from 'react';
import DOMPurify from 'dompurify';
import {Code,Eye,Copy,Download,ZoomIn,ZoomOut,Maximize,Check,AlertCircle} from 'lucide-react';
import {Markdown} from './markdown';

const viewAction=(canvas,act,patch)=>act('canvas.view',{id:canvas.id,patch});
function useReport(canvas,act,part='preview'){
 const ref=useRef(act);ref.current=act;
 return useMemo(()=> (status,message='')=>{if(canvas.id)ref.current('canvas.report',{id:canvas.id,part,status,message:String(message).slice(0,2000)})},[canvas.id,part]);
}
function cleanSvg(svg){
 return DOMPurify.sanitize(svg,{USE_PROFILES:{svg:true,svgFilters:true},FORBID_TAGS:['foreignObject','script','image','a','use','animate','set'],FORBID_ATTR:['href','xlink:href']});
}
let mermaidPromise;
export function Diagram({kind,source,canvas,act,part='preview',embedded=false}){
 const id=useId().replace(/[^a-zA-Z0-9]/g,''),report=useReport(canvas,act,part),[result,setResult]=useState({}),drag=useRef(null),frame=useRef(null);
 const view=canvas.view||{},zoom=view.zoom||1,panX=view.panX||0,panY=view.panY||0;
 useEffect(()=>{
  let active=true,worker,timer;setResult({});report('pending');
  const complete=value=>{if(!active)return;clearTimeout(timer);worker?.terminate();setResult(value);report(value.error?'error':'ready',value.error||'Diagram rendered')};
  if(source.length>50000){complete({error:'Diagrams must be 50,000 characters or smaller.'});return}
  if(kind==='dot'){
   worker=new Worker(new URL('./graphviz.worker.js',import.meta.url),{type:'module'});
   timer=setTimeout(()=>complete({error:'Diagram layout took too long. Simplify the graph and try again.'}),15000);
   worker.onmessage=e=>complete(e.data);worker.onerror=e=>complete({error:e.message||'Graph renderer failed'});
   worker.postMessage({source,engine:view.engine||'dot'});
  }else{
   mermaidPromise ||= import('mermaid').then(({default:m})=>{m.initialize({startOnLoad:false,securityLevel:'strict',maxTextSize:50000,maxEdges:500,suppressErrorRendering:true,theme:'default',flowchart:{htmlLabels:false},htmlLabels:false});return m});
   mermaidPromise.then(m=>m.render(`canvas${id}`,source)).then(({svg})=>complete({svg})).catch(e=>complete({error:e.message||String(e)}));
  }
  return()=>{active=false;clearTimeout(timer);worker?.terminate()};
 },[kind,source,view.engine,id,report]);
 const uri=useMemo(()=>result.svg?'data:image/svg+xml;charset=utf-8,'+encodeURIComponent(cleanSvg(result.svg)):null,[result.svg]);
 const set=patch=>viewAction(canvas,act,patch),selected=result.nodes?.find(n=>n.name===view.node);
 return <div className={`a-diagram ${embedded?'embedded':''}`}>
  {!embedded&&<div className="a-canvas-toolbar">
   <button type="button" className="a-icon" aria-label="Zoom out" data-action="canvas.view" onClick={()=>set({zoom:Math.max(.2,zoom-.2)})}><ZoomOut/></button><span>{Math.round(zoom*100)}%</span>
   <button type="button" className="a-icon" aria-label="Zoom in" data-action="canvas.view" onClick={()=>set({zoom:Math.min(4,zoom+.2)})}><ZoomIn/></button>
   <button type="button" className="a-soft" data-action="canvas.view" onClick={()=>set({zoom:1,panX:0,panY:0})}><Maximize/>Fit</button>
   {kind==='dot'&&<select aria-label="Graph layout" data-action="canvas.view" value={view.engine||'dot'} onChange={e=>set({engine:e.target.value,node:''})}>{['dot','neato','fdp','sfdp','circo','twopi'].map(engine=><option key={engine}>{engine}</option>)}</select>}
  </div>}
  {result.error?<div className="a-canvas-result error" role="alert"><AlertCircle/><span>{result.error}</span></div>:uri?<div ref={frame} className="a-diagram-stage" tabIndex={0} aria-label="Diagram. Drag to pan; arrow keys move the view." onKeyDown={e=>{const moves={ArrowLeft:[-30,0],ArrowRight:[30,0],ArrowUp:[0,-30],ArrowDown:[0,30]};if(moves[e.key]){e.preventDefault();set({panX:panX+moves[e.key][0],panY:panY+moves[e.key][1]})}}} onPointerDown={e=>{if(embedded)return;drag.current={x:e.clientX,y:e.clientY};e.currentTarget.setPointerCapture(e.pointerId)}} onPointerUp={e=>{if(!drag.current)return;set({panX:Math.max(-10000,Math.min(10000,panX+e.clientX-drag.current.x)),panY:Math.max(-10000,Math.min(10000,panY+e.clientY-drag.current.y))});drag.current=null}}><img alt={canvas.title||'Diagram'} draggable="false" src={uri} style={{transform:embedded?undefined:`translate(${panX}px,${panY}px) scale(${zoom})`}}/></div>:<p role="status">Drawing diagram…</p>}
  {!embedded&&result.nodes?.length>0&&<div className="a-node-inspector"><label>Inspect a node<select aria-label="Inspect graph node" data-action="canvas.view" value={view.node||''} onChange={e=>set({node:e.target.value})}><option value="">Choose a node…</option>{result.nodes.map(n=><option key={n.name} value={n.name}>{n.name}</option>)}</select></label>{selected&&<dl>{Object.entries(selected).map(([key,value])=><React.Fragment key={key}><dt>{key}</dt><dd>{typeof value==='object'?JSON.stringify(value):String(value)}</dd></React.Fragment>)}</dl>}</div>}
 </div>;
}
function CanvasMarkdown({canvas,act}){
 const overrides=useMemo(()=>({pre:({children})=><div className="a-canvas-code-block">{children}</div>,code:({className,children,node})=>{
  const language=/language-(\w+)/.exec(className||'')?.[1],source=String(children).replace(/\n$/,'');
  return ['mermaid','dot','graphviz'].includes(language)?<Diagram kind={language==='mermaid'?'mermaid':'dot'} source={source} canvas={canvas} act={act} embedded part={`fence-${node?.position?.start?.offset||0}`}/>:<code className={className}>{children}</code>;
 }}),[canvas.id,act]);
 return <Markdown text={canvas.content} overrides={overrides}/>;
}
function CodePreview({text}){
 const [html,setHtml]=useState('');
 useEffect(()=>{let current=true;setHtml('');if(text.length<100000)import('highlight.js').then(({default:h})=>{const result=h.highlightAuto(text).value;if(current)setHtml(result)});return()=>{current=false}},[text]);
 return html?<pre className="a-canvas-code"><code dangerouslySetInnerHTML={{__html:html}}/></pre>:<pre className="a-canvas-code">{text}</pre>;
}
export function CanvasViewer({canvas,act}){
 const report=useReport(canvas,act),view=canvas.view||{},source=canvas.content||'',rich=!['text','image'].includes(canvas.kind);
 const parsed=useMemo(()=>{if(!['json','jsonl'].includes(canvas.kind))return{};try{return{value:canvas.kind==='json'?JSON.parse(source):source.split('\n').filter(l=>l.trim()).map(l=>JSON.parse(l))}}catch(e){return{error:e.message}}},[canvas.kind,source]);
 useEffect(()=>{if(!['html','babylon','image','dot','mermaid'].includes(canvas.kind))report(parsed.error?'error':'ready',parsed.error||'Preview ready')},[canvas.id,canvas.kind,parsed.error,report]);
 const reports=Object.values(canvas.renderReports||{}),error=reports.find(r=>r.status==='error'),pending=reports.some(r=>r.status==='pending');
 return <div className="a-canvas-viewer">
  <div className="a-canvas-toolbar">
   {rich&&<><button type="button" className="a-soft" aria-pressed={!view.source} data-action="canvas.view" onClick={()=>viewAction(canvas,act,{source:false})}><Eye/>Preview</button><button type="button" className="a-soft" aria-pressed={!!view.source} data-action="canvas.view" onClick={()=>viewAction(canvas,act,{source:true})}><Code/>Source</button></>}
   <span className="a-canvas-format">{canvas.kind}</span>
   <button type="button" className="a-icon" aria-label="Copy canvas source" data-action="canvas.copy" onClick={()=>act('canvas.copy',{id:canvas.id})}><Copy/></button>
   <button type="button" className="a-icon" aria-label="Download canvas source" data-action="canvas.download" onClick={()=>act('canvas.download',{id:canvas.id})}><Download/></button>
  </div>
  <div className="a-canvas-preview">
   {view.source?(canvas.contentResource?<StoredSource canvas={canvas}/>:<CodePreview text={source}/>):['html','babylon'].includes(canvas.kind)?<HtmlPreview canvas={canvas} act={act}/>:['mermaid','dot'].includes(canvas.kind)?<Diagram kind={canvas.kind} source={source} canvas={canvas} act={act}/>:canvas.kind==='markdown'?<CanvasMarkdown canvas={canvas} act={act}/>:canvas.kind==='image'?<img className="a-canvas-image" src={source} alt={canvas.title||'Workspace image'} onLoad={()=>report('ready','Image loaded')} onError={()=>report('error','This image could not be decoded')}/>:['json','jsonl'].includes(canvas.kind)?parsed.error?<div className="a-canvas-result error" role="alert">{parsed.error}</div>:<StructuredData value={parsed.value} canvas={canvas} act={act}/>:canvas.kind==='code'?<CodePreview text={source}/>:<pre className="a-canvas-plain">{source}</pre>}
  </div>
  <div className={`a-canvas-result ${error?'error':pending?'':'success'}`} role="status">{error?<AlertCircle/>:pending?null:<Check/>}<span>{error?error.message||'Preview needs attention':pending?'Rendering…':canvas.renderReports?.clipboard?.message|| (['html','babylon'].includes(canvas.kind)?'Isolated HTML preview':'Ready')}</span></div>
 </div>;
}
function StructuredData({value,canvas,act}){
 const query=canvas.view?.query||'',rows=Array.isArray(value)?value:[value],filtered=rows.map((v,i)=>({v,i})).filter(({v})=>!query||JSON.stringify(v).toLowerCase().includes(query.toLowerCase()));
 return <div className="a-canvas-data"><input aria-label="Filter data records" type="search" placeholder="Filter records…" value={query} data-action="canvas.view" onChange={e=>viewAction(canvas,act,{query:e.target.value})}/><small>{filtered.length} of {rows.length} records</small>{filtered.slice(0,200).map(({v,i})=><pre key={i}>{JSON.stringify(v,null,2)}</pre>)}{filtered.length>200&&<p>Showing the first 200 matches. Narrow the filter to see more.</p>}</div>;
}

function HtmlPreview({canvas,act}){
 const frame=useRef(null),report=useReport(canvas,act),actRef=useRef(act),sent=useRef(null);actRef.current=act;
 useEffect(()=>{
  report('pending','Loading HTML document');
  const receive=e=>{
   if(e.source!==frame.current?.contentWindow||e.data?.id!==canvas.id)return;
   if(e.data.type==='canvas-render'&&['ready','error'].includes(e.data.status))report(e.data.status,String(e.data.message||'').slice(0,2000));
   if(e.data.type==='canvas-snapshot'){
    const data=e.data.document;if(!data||typeof data.text!=='string'||!Array.isArray(data.controls))return;
    const document={text:data.text.slice(0,16000),controls:data.controls.slice(0,100).map(c=>({id:String(c.id||'').slice(0,100),tag:String(c.tag||'').slice(0,30),type:String(c.type||'').slice(0,30),label:String(c.label||'').slice(0,200),value:String(c.value||'').slice(0,4000),disabled:!!c.disabled}))};
    actRef.current('canvas.snapshot',{id:canvas.id,document});
   }
  };
  window.addEventListener('message',receive);return()=>window.removeEventListener('message',receive);
 },[canvas.id,report]);
 useEffect(()=>{const request=canvas.interaction;if(request&&sent.current!==request.requestId){sent.current=request.requestId;frame.current?.contentWindow?.postMessage({type:'canvas-interact',...request},'*')}},[canvas.interaction]);
 return <iframe ref={frame} title={canvas.title||'Interactive canvas'} className="a-canvas-html" sandbox="allow-scripts" referrerPolicy="no-referrer" src={clientUrl(`/api/canvas/${canvas.id}/document`)}/>;
}


function StoredSource({canvas}){
 const [result,setResult]=useState({});
 useEffect(()=>{const controller=new AbortController();setResult({});fetch(clientUrl(`/api/canvas/${canvas.id}/source`),{signal:controller.signal}).then(async response=>{if(!response.ok)throw Error('The saved source could not be loaded.');return response.text()}).then(text=>setResult({text})).catch(error=>{if(error.name!=='AbortError')setResult({error:error.message})});return()=>controller.abort()},[canvas.id]);
 return result.error?<p role="alert">{result.error}</p>:result.text===undefined?<p role="status">Loading saved source…</p>:<CodePreview text={result.text}/>;
}
