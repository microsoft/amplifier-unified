import React,{useEffect,useState} from 'react';
import {Check,Circle,Copy,LoaderCircle} from 'lucide-react';
import {request} from './api';
import {readDetail} from './detail-read';
import {detailLinks} from './timeline-data';
import {textValue} from './execution-content.js';

export function useExecutionField(node,field,open){
 const reference=node[field+'Detail'],key=JSON.stringify([node.id,field,reference||node[field]]);
 const [loaded,setLoaded]=useState(null),[failure,setFailure]=useState(null),[attempt,setAttempt]=useState(0);
 const value=loaded?.key===key?loaded.value:node[field],error=failure?.key===key?failure.message:'';
 useEffect(()=>{
  if(!open||!reference||loaded?.key===key)return;
  const controller=new AbortController();setFailure(null);
  readDetail(reference,request,controller.signal).then(value=>{if(!controller.signal.aborted)setLoaded({key,value})})
   .catch(e=>{if(!controller.signal.aborted)setFailure({key,message:e.message})});
  return()=>controller.abort();
 },[open,key,attempt,loaded?.key]);
 return {value,error,incomplete:!!reference&&loaded?.key!==key,loading:!!reference&&loaded?.key!==key&&!error,retry:()=>setAttempt(n=>n+1)};
}
function CopyText({text,label,disabled=false}){
 const [status,setStatus]=useState('');
 useEffect(()=>setStatus(''),[text]);
 return <><button type="button" className="a-link" disabled={disabled} aria-label={`Copy ${label.toLowerCase()}`} onClick={async()=>{try{await navigator.clipboard.writeText(text);setStatus('Copied')}catch{setStatus('Select the text to copy it manually.')}}}><Copy/>{status==='Copied'?'Copied':'Copy'}</button>{status&&<span className="a-caption" role="status">{status}</span>}</>;
}
export function ExecutionText({text}){
 const [full,setFull]=useState(false),value=textValue(text),lines=value.split('\n'),preview=lines.slice(0,40).join('\n').slice(0,3500),long=preview.length<value.length;
 useEffect(()=>setFull(false),[value]);
 return <><pre><code>{full?value:preview}</code></pre>{long&&<button type="button" className="a-link a-execution-more" onClick={()=>setFull(!full)}>{full?'Show less':`Show full content (${value.length.toLocaleString()} characters)`}</button>}</>;
}
export function ExecutionBlock({label,text,meta,children,copy=true,loading=false}){
 if(text==null&&!children)return null;
 return <section className="a-execution-payload"><header><span>{label}</span>{meta!=null&&<span>{textValue(meta)}</span>}{copy&&<CopyText text={textValue(text)} label={label} disabled={loading}/>}</header>{children||<ExecutionText text={text}/>}</section>;
}
export function FieldStatus({label,field}){
 return field.error?<p role="alert" className="a-execution-load-error">{field.error} <button type="button" className="a-link" onClick={field.retry}>Retry {label.toLowerCase()}</button></p>:field.loading?<small role="status">Loading complete {label.toLowerCase()}…</small>:null;
}
function Patch({action}){
 const [full,setFull]=useState(false),rows=action.rows;
 return <ExecutionBlock label={action.path||'Patch'} text={action.diff} meta={action.done?'Applied':action.running?'In progress':'Requested changes'}>
  <div className="a-execution-diff" aria-label="File changes">{(full?rows:rows.slice(0,60)).map((row,index)=>row.type==='header'?/^\*\*\* (Begin|End) Patch$/.test(row.text)?null:<div key={index} className="a-execution-diff-header">{row.file||row.text}</div>:<div key={index} className={`a-execution-diff-row ${row.type}`}><span className="a-execution-line-number">{row.old}</span><span className="a-execution-line-number">{row.new}</span><span>{row.type==='add'?'+':row.type==='remove'?'−':' '}</span><code>{row.text}</code></div>)}</div>
  {rows.length>60&&<button type="button" className="a-link a-execution-more" onClick={()=>setFull(!full)}>{full?'Show less':`Show all ${rows.length} patch lines`}</button>}
 </ExecutionBlock>;
}
export function ToolContent({node,action,input,output,error}){
 const a=action,body=textValue(a.output),streams=typeof a.out.stdout==='string'||typeof a.out.stderr==='string',hasOutput=output.value!=null;
 let content;
 if(a.kind==='command')content=<><ExecutionBlock label="Command" text={a.command} meta={a.args.working_directory||a.args.cwd}/>{hasOutput&&(streams?<><ExecutionBlock label="Output" text={a.out.stdout||'No output'} meta={a.exit!=null?`Exit ${a.exit}`:null}/>{a.out.stderr&&<ExecutionBlock label="Standard error" text={a.out.stderr}/>}</>:<ExecutionBlock label="Output" text={body||'No output'} loading={output.incomplete}/>)}</>;
 else if(a.kind==='patch')content=<><Patch action={a}/>{hasOutput&&<ExecutionBlock label="Result" text={body} loading={output.incomplete}/>}</>;
 else if(a.kind==='read'||a.kind==='write')content=<><ExecutionBlock label={a.kind==='read'?'File content':a.done?'Written content':'Requested content'} text={a.kind==='read'&&a.failed?null:a.content||null} loading={a.kind==='read'?output.incomplete:input.incomplete}/>{hasOutput&&(!a.content||a.failed||a.kind==='write')&&<ExecutionBlock label="Result" text={body} loading={output.incomplete}/>}</>;
 else if(a.kind==='tasks')content=<><section className="a-execution-payload"><header><span>{a.done?'Task list':'Requested task list'}</span><span>{a.preview}</span></header><ul className="a-execution-checklist">{a.tasks.map((task,index)=>{const status=typeof task?.status==='string'?task.status:'unknown',Icon=status==='completed'?Check:status==='in_progress'?LoaderCircle:Circle;return <li key={index} data-status={status}><Icon aria-label={status}/><span>{textValue(task?.content||task?.activeForm||task?.title||task)}</span><small>{status.replaceAll('_',' ')}</small></li>})}</ul></section>{hasOutput&&<ExecutionBlock label="Result" text={body} loading={output.incomplete}/>}</>;
 else if(a.kind==='delegate')content=<><ExecutionBlock label="Task sent" text={a.task}/>{hasOutput&&<ExecutionBlock label="Result" text={body} loading={output.incomplete}/>}</>;
 else content=<><ExecutionBlock label={a.kind==='app'?a.target:'Input'} text={input.value} loading={input.incomplete}/><ExecutionBlock label="Result" text={output.value} loading={output.incomplete}/></>;
 return <>{content}<ExecutionBlock label="Error" text={error.value} loading={error.incomplete}/>{[['Input',input],['Result',output],['Error',error]].map(([label,field])=><FieldStatus key={label} label={label} field={field}/>)}
  {detailLinks(output.value).map(url=><a key={url} className="a-execution-result-link" href={url} target="_blank" rel="noopener noreferrer">{url}</a>)}
  {input.value==null&&output.value==null&&error.value==null&&!input.loading&&!output.loading&&!error.loading&&<small>{a.running?'Waiting for action content…':'Action content was not saved for this step.'}</small>}
 </>;
}
