import React, {createContext,memo,useContext} from 'react';
import ReactMarkdown, {defaultUrlTransform} from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {sourceSpans} from './canvas-reference.js';
import {writingParts} from './writing-blocks.js';
import {WritingBlock} from './writing-block.js';
import {canvasReference,CanvasReferenceLink} from './canvas-links.js';
import {localFilePath,LocalFileLink,remarkLocalFiles} from './local-file-links.js';
import {BlockCopy,copySourceBlocks} from './block-copy.js';

const components={
 a:({node,href,children,...props})=>React.createElement('a',{...props,href,target:href?.startsWith('#')?undefined:'_blank',rel:'noopener noreferrer'},children),
 // Loading remote images can disclose the reader's address. Offer an explicit link instead.
 img:({src,alt})=>React.createElement('a',{href:src,target:'_blank',rel:'noopener noreferrer'},alt?`Image: ${alt}`:'Open image'),
 table:({node,...props})=>React.createElement('div',{className:'a-table-scroll'},React.createElement('table',props)),
};
const FileContext=createContext(null);
const BlockContext=createContext(null);
function CopyableMarkdownBlock({tag,node,...props}){
 const {payloads,overrides}=useContext(BlockContext);
 const payload=payloads.get(node?.position?.start?.offset)||{unavailable:'Exact block source is unavailable.'};
 const renderer=overrides[tag];
 const body=renderer?React.createElement(renderer,{node,...props}):React.createElement(tag,props);
 return React.createElement(BlockCopy,{...payload,label:tag==='pre'?'Copy code block':'Copy quote block'},body);
}
const CopyPre=props=>React.createElement(CopyableMarkdownBlock,{...props,tag:'pre'});
const CopyQuote=props=>React.createElement(CopyableMarkdownBlock,{...props,tag:'blockquote'});
function FileAnchor({node,href,children,...props}){
 const context=useContext(FileContext),reference=context&&canvasReference(href),path=context?.workspace&&localFilePath(href);
 if(reference)return React.createElement(CanvasReferenceLink,{key:href,reference,context},children);
 return path?React.createElement(LocalFileLink,{key:JSON.stringify([context.sessionId,context.workspace,path]),path,context},children):React.createElement(components.a,{href,...props},children);
}
export const Markdown=memo(function Markdown({text,className='',overrides={},writingContext,fileContext,mapSource=false}){
 const canvasActions=!!(fileContext?.sessionId&&fileContext?.act),fileActions=!!(canvasActions&&fileContext.workspace);
 const original=String(text||'');
 const render=(value,offset,copySource=value)=>{
  const payloads=new Map();
  return React.createElement(BlockContext.Provider,{value:{payloads,overrides}},React.createElement(ReactMarkdown,{remarkPlugins:[remarkGfm,copySourceBlocks(copySource,payloads),...(mapSource&&Number.isInteger(offset)?[sourceSpans(value,offset)]:[]),...(fileActions?[remarkLocalFiles]:[])],skipHtml:true,urlTransform:url=>canvasActions&&canvasReference(url)||fileActions&&localFilePath(url)?url:defaultUrlTransform(url),components:{...components,...(canvasActions?{a:FileAnchor}:{}),...overrides,pre:CopyPre,blockquote:CopyQuote}},value));
 };
 return React.createElement(FileContext.Provider,{value:canvasActions?fileContext:null},React.createElement('div',{className:`a-markdown ${className}`.trim()},
  ...writingParts(text,{offsets:true}).map((part,index)=>{
   const renderPart=value=>{
    const unchanged=value===part.text,end=part.sourceOffset+part.text.length;
    const after=unchanged?/^(?:\r\n|\n|\r)/.exec(original.slice(end,end+2))?.[0]||'':'';
    return render(value,unchanged?part.sourceOffset:undefined,value+after);
   };
   return part.type==='writing'?React.createElement(WritingBlock,{key:part.id+':'+index,part,context:writingContext,render:renderPart}):React.createElement(React.Fragment,{key:index},renderPart(part.text));
  })));
});
