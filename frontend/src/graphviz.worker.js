import {instance} from '@viz-js/viz';
let viz;
self.onmessage=async({data})=>{
 try{
  viz ||= await instance();
  const options={engine:data.engine||'dot',graphAttributes:{bgcolor:'transparent',pad:'0.3'},nodeAttributes:{fontname:'Arial',shape:'box',style:'rounded,filled',fillcolor:'#eef2ff',color:'#818cf8',fontcolor:'#172033'},edgeAttributes:{fontname:'Arial',color:'#64748b'}};
  const result=viz.renderFormats(data.source,['svg','json'],options);
  if(result.status!=='success')throw Error(result.errors.map(e=>e.message).join('\n'));
  const graph=JSON.parse(result.output.json),hidden=new Set(['_gvid','pos','width','height','rects','vertices']);
  const nodes=(graph.objects||[]).filter(n=>!n.nodes).slice(0,1000).map(n=>Object.fromEntries(Object.entries(n).filter(([k])=>!k.startsWith('_')&&!hidden.has(k))));
  self.postMessage({svg:result.output.svg,nodes});
 }catch(e){self.postMessage({error:e.message})}
};
