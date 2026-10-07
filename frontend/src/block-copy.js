import React,{useEffect,useId,useRef,useState} from 'react';
import {writeClipboardText,clipboardNotice} from './clipboard-copy.js';

export function CopyControl({text,label='Copy block',identity='',unavailable='',onResult}){
 const id=useId(),scope=useRef(null),mounted=useRef(true),[result,setResult]=useState(null);
 if(!scope.current||scope.current.text!==text||scope.current.identity!==identity)scope.current={text,identity,busy:false};
 const current=scope.current,shown=result?.scope===current?result:null;
 useEffect(()=>{mounted.current=true;return()=>{mounted.current=false}},[]);
 async function copy(){
  if(current.busy||unavailable)return;
  current.busy=true;setResult({scope:current,message:'Copying…',pending:true});
  try{
   // Capture and dispatch this version now, never read a later prop after await.
   await writeClipboardText(current.text);
   if(mounted.current&&scope.current===current){setResult({scope:current,message:'Copied.',pending:false});onResult?.('ready','Copied canvas source.')}
  }catch(error){
   if(mounted.current&&scope.current===current){setResult({scope:current,message:clipboardNotice(error),pending:false});onResult?.('error',clipboardNotice(error))}
  }finally{current.busy=false}
 }
 return React.createElement('div',{className:'a-block-copy-control'},
  React.createElement('button',{type:'button',className:'a-soft',onClick:copy,disabled:!!unavailable||!!shown?.pending,'aria-label':label,'aria-describedby':id,'aria-busy':!!shown?.pending},shown?.pending?'Copying…':label),
  React.createElement('span',{id,role:'status','aria-live':'polite','aria-atomic':true,className:'a-block-copy-notice'},unavailable||shown?.message||''));
}

export function BlockCopy({text,label,identity,unavailable,children}){
 return React.createElement('div',{className:'a-block-copy','data-copy-block':true},
  React.createElement(CopyControl,{text,label,identity,unavailable}),children);
}

// Keep every original line ending. Positions refer to the selected source,
// not the normalized mdast value or the rendered DOM.
function lines(raw){
 return [...raw.matchAll(/([^\r\n]*)(\r\n|\n|\r|$)/g)].filter(match=>match[0]).map(match=>({body:match[1],eol:match[2]}));
}
function unquote(body,depth){
 for(let i=0;i<depth;i++){
  const prefix=/^ {0,3}>[ \t]?/.exec(body);
  // Lazy continuation lines have no quote marker.
  if(!prefix)break;
  body=body.slice(prefix[0].length);
 }
 return body;
}
function unindent(body){
 let index=0,column=0;
 while(index<body.length&&column<4){
  if(body[index]===' ')column++;
  else if(body[index]==='\t')column+=4-column%4;
  else break;
  index++;
 }
 if(column<4&&body.trim())return null;
 return body.slice(index);
}
export function blockPayload(source,node,ancestors=[]){
 const start=node.position?.start?.offset,end=node.position?.end?.offset;
 if(!Number.isInteger(start)||!Number.isInteger(end)||start<0||end>source.length)return {unavailable:'Exact block source is unavailable.'};
 // List indentation depends on the marker width and tab stops. Do not guess.
 if(ancestors.some(parent=>parent.type==='listItem'))return {unavailable:'Exact block copy inside lists is not available. Select the block text to copy it.'};
 const quoteDepth=ancestors.filter(parent=>parent.type==='blockquote').length;
 const lineStart=Math.max(source.lastIndexOf('\n',start-1),source.lastIndexOf('\r',start-1))+1;
 const after=/^(?:\r\n|\n|\r)/.exec(source.slice(end,end+2))?.[0]||'';
 const selected=lines(source.slice(lineStart,end)+after);
 selected.forEach(line=>{line.body=unquote(line.body,quoteDepth)});
 if(node.type==='blockquote'){
  // Remove this container's marker only; nested > syntax belongs to its
  // inner Markdown. A nested block's own control removes its ancestors too.
  return {text:selected.map(line=>unquote(line.body,1)+line.eol).join('')};
 }
 if(node.type!=='code')return {unavailable:'This is not a copyable block.'};
 const opening=/^ {0,3}(`{3,}|~{3,})/.exec(selected[0]?.body||'');
 if(opening){
  const fence=opening[1],last=selected.at(-1)?.body||'';
  const close=new RegExp('^ {0,3}'+fence[0]+'{'+fence.length+',}[ \\t]*$');
  const closed=selected.length>1&&close.test(last);
  return {text:selected.slice(1,closed?-1:undefined).map(line=>line.body+line.eol).join('')};
 }
 const body=selected.map(line=>({body:unindent(line.body),eol:line.eol}));
 if(body.some(line=>line.body===null))return {unavailable:'Exact indented block source is unavailable. Select the block text to copy it.'};
 return {text:body.map(line=>line.body+line.eol).join('')};
}

// A per-render map keeps payloads out of HTML attributes and preserves the
// existing Canvas source-span plugin unchanged.
export function copySourceBlocks(source,payloads){
 return ()=>tree=>{
  const visit=(node,ancestors)=>{
   if(['code','blockquote'].includes(node.type))payloads.set(node.position?.start?.offset,blockPayload(source,node,ancestors));
   for(const child of node.children||[])visit(child,[...ancestors,node]);
  };
  visit(tree,[]);
 };
}