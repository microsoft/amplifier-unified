import {defineModule,useCanvas} from '../../packages/shell-sdk/index.js';

// Only the public SDK and host-supplied React. The saved source is never edited.
export default defineModule(({React})=>function ReadingView({host}){
 const h=React.createElement,{resource,view}=useCanvas(React,host),[error,setError]=React.useState('');
 if(!resource)return h('p',null,'Choose a document to read.');
 const paragraphs=(resource.content||'').split(/\n\s*\n/),scale=Number(view.readerScale)||18;
 return h('article',{'aria-label':'Reading view',style:{padding:20,lineHeight:1.7,fontSize:scale,color:'var(--a-ink)',background:'var(--a-surface)'}},
  h('div',{style:{display:'flex',alignItems:'center',gap:10,fontSize:12}},h('strong',null,'Reading view'),
   h('label',null,'Text size ',h('select',{'aria-label':'Reader text size',value:scale,onChange:event=>host.dispatch('view.update',{patch:{readerScale:Number(event.target.value)}}).catch(error=>setError(error.message))},...[16,18,22,26].map(size=>h('option',{key:size,value:size},String(size)))))),
  ...paragraphs.map((text,index)=>{const heading=/^(#{1,3})\s+(.+)$/.exec(text);return h(heading?'h'+heading[1].length:'p',{key:index,style:{whiteSpace:'pre-wrap',overflowWrap:'anywhere'}},heading?heading[2]:text)}),
  error&&h('p',{role:'alert'},error));
});
