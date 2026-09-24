import React, {createContext,memo,useContext} from 'react';
import ReactMarkdown, {defaultUrlTransform} from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {writingParts} from './writing-blocks.js';
import {WritingBlock} from './writing-block.js';
import {localFilePath,LocalFileLink,remarkLocalFiles} from './local-file-links.js';

const components={
 a:({node,href,children,...props})=>React.createElement('a',{...props,href,target:href?.startsWith('#')?undefined:'_blank',rel:'noopener noreferrer'},children),
 // Loading remote images can disclose the reader's address. Offer an explicit link instead.
 img:({src,alt})=>React.createElement('a',{href:src,target:'_blank',rel:'noopener noreferrer'},alt?`Image: ${alt}`:'Open image'),
 table:({node,...props})=>React.createElement('div',{className:'a-table-scroll'},React.createElement('table',props)),
};
const FileContext=createContext(null);
function FileAnchor({node,href,children,...props}){
 const context=useContext(FileContext),path=context&&localFilePath(href);
 return path?React.createElement(LocalFileLink,{key:JSON.stringify([context.sessionId,context.workspace,path]),path,context},children):React.createElement(components.a,{href,...props},children);
}
export const Markdown=memo(function Markdown({text,className='',overrides={},writingContext,fileContext}){
 const fileActions=!!(fileContext?.workspace&&fileContext?.sessionId&&fileContext?.act);
 const render=value=>React.createElement(ReactMarkdown,{remarkPlugins:[remarkGfm,...(fileActions?[remarkLocalFiles]:[])],skipHtml:true,urlTransform:url=>fileActions&&localFilePath(url)?url:defaultUrlTransform(url),components:{...components,...(fileActions?{a:FileAnchor}:{}),...overrides}},value);
 return React.createElement(FileContext.Provider,{value:fileActions?fileContext:null},React.createElement('div',{className:`a-markdown ${className}`.trim()},
  ...writingParts(text).map((part,index)=>part.type==='writing'?React.createElement(WritingBlock,{key:part.id+':'+index,part,context:writingContext,render}):React.createElement(React.Fragment,{key:index},render(part.text)))));
});
