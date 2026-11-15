// Reusable against a synthetic fixture or an already-open installed-app banner.
// This observes geometry/focus only; it never activates recovery/report/dismiss.
import assert from 'node:assert/strict';

export async function assertConversationErrorGeometry({banner,composer,label='conversation error'}){
 const box=await banner.boundingBox(),column=await composer.boundingBox();
 assert.ok(box&&column,`${label}: banner and composer are rendered`);
 assert.ok(Math.abs(box.x-column.x)<=1&&Math.abs(box.width-column.width)<=1,`${label}: banner shares the composer's exact column (${JSON.stringify({box,column})})`);
 const geometry=await banner.evaluate(node=>{
  const rect=node.getBoundingClientRect(),style=getComputedStyle(node);
  const controls=[...node.querySelectorAll('button,summary')].filter(child=>child.getClientRects().length);
  const boxes=controls.map(child=>{const box=child.getBoundingClientRect();return {text:child.getAttribute('aria-label')||child.textContent,box:{left:box.left,right:box.right,top:box.top,bottom:box.bottom},clipped:child.scrollWidth>child.clientWidth+1||child.scrollHeight>child.clientHeight+1}});
  const walker=document.createTreeWalker(node,NodeFilter.SHOW_TEXT),textRects=[];
  for(let text=walker.nextNode();text;text=walker.nextNode()){
   if(!text.textContent.trim()||!text.parentElement.getClientRects().length)continue;
   const range=document.createRange();range.selectNodeContents(text);
   for(const box of range.getClientRects())if(box.width&&box.height)textRects.push({left:box.left,right:box.right,top:box.top,bottom:box.bottom});
  }
  const dismiss=node.querySelector('.a-conversation-trouble-dismiss')?.getBoundingClientRect();
  const intersects=(a,b)=>Math.min(a.right,b.right)-Math.max(a.left,b.left)>1&&Math.min(a.bottom,b.bottom)-Math.max(a.top,b.top)>1;
  return {width:rect.width,maxWidth:style.maxWidth,horizontalOverflow:node.scrollWidth>node.clientWidth+1,
   outsideText:textRects.filter(box=>box.left<rect.left-1||box.right>rect.right+1),
   dismissTextIntersections:dismiss?textRects.filter(box=>intersects(box,dismiss)):[],
   overlappingControls:boxes.flatMap((a,index)=>boxes.slice(index+1).filter(b=>intersects(a.box,b.box)).map(b=>[a.text,b.text])),controls:boxes};
 });
 assert.equal(geometry.horizontalOverflow,false,`${label}: banner has no horizontal overflow`);
 assert.deepEqual(geometry.outsideText,[],`${label}: actual visible text ranges remain within banner width`);
 assert.deepEqual(geometry.dismissTextIntersections,[],`${label}: dismiss has its own space, clear of text`);
 assert.deepEqual(geometry.overlappingControls,[],`${label}: controls do not overlap`);
 assert.deepEqual(geometry.controls.filter(control=>control.clipped),[],`${label}: control labels are not clipped`);
 for(const control of geometry.controls)assert.ok(control.box.bottom-control.box.top>=44,`${label}: ${control.text} retains a 44px target`);
 return {label,banner:box,composer:column,...geometry};
}

export async function assertConversationErrorFocus(control,label){
 await control.focus();
 const result=await control.evaluate(node=>{
  node.scrollIntoView({block:'nearest',inline:'nearest'});
  const box=node.getBoundingClientRect(),style=getComputedStyle(node);
  let top=0,left=0,right=innerWidth,bottom=innerHeight;
  for(let ancestor=node.parentElement;ancestor;ancestor=ancestor.parentElement){
   const css=getComputedStyle(ancestor),rect=ancestor.getBoundingClientRect();
   if(/auto|scroll|hidden|clip/.test(css.overflowY)){top=Math.max(top,rect.top+ancestor.clientTop);bottom=Math.min(bottom,rect.top+ancestor.clientTop+ancestor.clientHeight)}
   if(/auto|scroll|hidden|clip/.test(css.overflowX)){left=Math.max(left,rect.left+ancestor.clientLeft);right=Math.min(right,rect.left+ancestor.clientLeft+ancestor.clientWidth)}
  }
  const hit=document.elementFromPoint((box.left+box.right)/2,(box.top+box.bottom)/2);
  return {focused:document.activeElement===node,outline:style.outlineStyle!=='none'&&parseFloat(style.outlineWidth)>0||style.boxShadow!=='none',reachable:box.top>=top-1&&box.bottom<=bottom+1&&box.left>=left-1&&box.right<=right+1,hit:!!hit&&(node===hit||node.contains(hit))};
 });
 assert.equal(result.focused,true,`${label}: can receive keyboard focus`);
 assert.equal(result.outline,true,`${label}: focus indicator is not suppressed`);
 assert.equal(result.reachable,true,`${label}: focused control can scroll fully inside clipping ancestors`);
 assert.equal(result.hit,true,`${label}: focused control is not covered`);
 return result;
}
