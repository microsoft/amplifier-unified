import React, {memo} from 'react';
import ReactMarkdown, {defaultUrlTransform} from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {writingParts} from './writing-blocks.js';
import {WritingBlock} from './writing-block.js';

const components={
 a:({node,href,children,...props})=>React.createElement('a',{...props,href,target:href?.startsWith('#')?undefined:'_blank',rel:'noopener noreferrer'},children),
 // Loading remote images can disclose the reader's address. Offer an explicit link instead.
 img:({src,alt})=>React.createElement('a',{href:src,target:'_blank',rel:'noopener noreferrer'},alt?`Image: ${alt}`:'Open image'),
 table:({node,...props})=>React.createElement('div',{className:'a-table-scroll'},React.createElement('table',props)),
};
export const Markdown=memo(function Markdown({text,className='',overrides={},writingContext}){
 const render=value=>React.createElement(ReactMarkdown,{remarkPlugins:[remarkGfm],skipHtml:true,urlTransform:defaultUrlTransform,components:{...components,...overrides}},value);
 return React.createElement('div',{className:`a-markdown ${className}`.trim()},
  ...writingParts(text).map((part,index)=>part.type==='writing'?React.createElement(WritingBlock,{key:part.id+':'+index,part,context:writingContext,render}):React.createElement(React.Fragment,{key:index},render(part.text))));
});
