// Shared geometry assertions for source fixtures and installed-app qualification.
// Reads the rendered DOM only; caller explicitly opens local disclosures first.
import assert from 'node:assert/strict';
export async function assertWorkHierarchy(turn,label='expanded work'){
 const geometry=await turn.evaluate(node=>{
  const box=element=>{const r=element.getBoundingClientRect();return {left:r.left,right:r.right,width:r.width,top:r.top,bottom:r.bottom}};
  const summary=node.querySelector(':scope>.a-execution-turn-line'),body=node.querySelector(':scope>.a-execution-turn-body,:scope>.a-work-group-body');
  const rows=[...body.querySelectorAll(':scope>.a-work-stream>.a-execution-node')];
  const siblings=rows.map(row=>({id:row.dataset.nodeId,owner:row.dataset.ownerId,box:box(row.querySelector(':scope>.a-execution-line')),label:box(row.querySelector(':scope>.a-execution-line>.a-execution-label'))}));
  const branch=body.firstElementChild?.classList.contains('a-work-stream')?body.firstElementChild:body;
  const parentLabel=summary?.querySelector('.a-execution-label');
  const texts=[];const walker=document.createTreeWalker(body,NodeFilter.SHOW_TEXT);
  for(let text=walker.nextNode();text;text=walker.nextNode())if(text.textContent.trim()&&text.parentElement.getClientRects().length&&!text.parentElement.closest('pre')){
   const range=document.createRange();range.selectNodeContents(text);for(const rect of range.getClientRects())if(rect.width&&rect.height)texts.push({left:rect.left,right:rect.right});
  }
  const childBodies=[...body.querySelectorAll('.a-execution-node>.a-execution-body')].map(child=>({parent:box(child.parentElement.querySelector(':scope>.a-execution-line')),box:box(child),padding:parseFloat(getComputedStyle(child).paddingInlineStart)}));
  return {turn:box(node),body:box(body),parentLabel:parentLabel?box(parentLabel):null,summary:summary?box(summary):null,branch:box(branch),siblings,childBodies,texts,horizontalOverflow:body.scrollWidth>body.clientWidth+1};
 });
 assert.ok(geometry.summary&&geometry.parentLabel,label+': parent summary is present');
 assert.ok(geometry.body.left>geometry.summary.left+3,label+': branch boundary is inset from parent');
 for(const row of geometry.siblings)assert.ok(row.label.left>=geometry.parentLabel.left+8,label+': child label is visibly inside parent label: '+row.id);
 for(const row of geometry.siblings.slice(1))assert.ok(Math.abs(row.label.left-geometry.siblings[0].label.left)<=1,label+': sibling label columns align');
 assert.equal(geometry.horizontalOverflow,false,label+': child body has no horizontal overflow');
 for(const child of geometry.childBodies){assert.ok(child.padding>=8,label+': grandchildren retain a bounded child inset');assert.ok(child.box.right<=geometry.body.right+1,label+': payload body stays in its branch')}
 for(const text of geometry.texts)assert.ok(text.left>=geometry.body.left-1&&text.right<=geometry.body.right+1,label+': rendered text remains within child width');
 assert.ok(geometry.body.width>=geometry.turn.width-32,label+': a turn branch consumes at most32px, preserving narrow text width');
 return geometry;
}
export async function assertDisclosureBody(details,bodySelector,label){
 const geometry=await details.evaluate((node,selector)=>{
  const summary=node.querySelector(':scope>summary'),body=node.querySelector(':scope>'+selector),outer=node.getBoundingClientRect();
  const b=body.getBoundingClientRect(),style=getComputedStyle(body),summaryStyle=getComputedStyle(summary),range=document.createRange();range.selectNodeContents(summary);
  const text=range.getBoundingClientRect();
  return {outer:{left:outer.left,right:outer.right},body:{left:b.left,right:b.right},summaryText:{left:text.left,right:text.right},inset:parseFloat(style.paddingInlineStart),bodyFontSize:style.fontSize,summaryFontSize:summaryStyle.fontSize,summaryPadding:summaryStyle.paddingInlineStart,markerPosition:summaryStyle.listStylePosition,overflow:body.scrollWidth>body.clientWidth+1};
 },bodySelector);
 const evidence='; geometry='+JSON.stringify(geometry);
 assert.ok(geometry.inset>=12,label+': scoped child-body inset'+evidence);
 assert.ok(geometry.body.left+geometry.inset>=geometry.summaryText.left,label+': child content does not start before summary text'+evidence);
 assert.equal(geometry.overflow,false,label+': child text/path wraps without overflow'+evidence);
 return geometry;
}
