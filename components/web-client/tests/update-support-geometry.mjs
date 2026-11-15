// Shared by canonical synthetic journeys and an installed-app audit. Observes
// current rendered controls only; does not collect/copy/save any diagnostics.
import assert from 'node:assert/strict';

export async function assertUpdateSupportGeometry(support,label){
 const geometry=await support.evaluate(node=>{
  const rect=node=>{const r=node.getBoundingClientRect();return {left:r.left,right:r.right,top:r.top,bottom:r.bottom}};
  const buttons=[...node.querySelectorAll('button')].map(button=>{
   const style=getComputedStyle(button),icon=button.querySelector('svg'),copy=button.querySelector('span');
   const range=document.createRange();range.selectNodeContents(copy);
   return {label:copy.textContent,box:rect(button),icon:rect(icon),direction:style.direction,
    text:[...range.getClientRects()].filter(r=>r.width&&r.height).map(r=>({left:r.left,right:r.right,top:r.top,bottom:r.bottom})),
    paddingStart:parseFloat(style.paddingInlineStart),paddingEnd:parseFloat(style.paddingInlineEnd),
    disabled:button.disabled,overflow:button.scrollWidth>button.clientWidth+1,whiteSpace:style.whiteSpace};
  });
  return {box:rect(node),buttons,minimumHeight:node.closest('[data-compact=true]')?48:matchMedia('(pointer:coarse)').matches?44:0};
 });
 assert.equal(geometry.buttons.length,2,label+' copy and save remain distinct controls');
 for(const button of geometry.buttons){
  assert.ok(button.text.length>0&&button.icon.right>button.icon.left,label+' measures real visible icon and glyph ranges');
  assert.ok(button.paddingStart>=12&&button.paddingEnd>=12,label+' painted button has inline clearance: '+JSON.stringify(button));
  assert.equal(button.overflow,false,label+' no clipped label');
  assert.ok(button.box.bottom-button.box.top>=geometry.minimumHeight,label+' retains responsive minimum target height');
  assert.ok(button.box.left>=geometry.box.left-1&&button.box.right<=geometry.box.right+1,label+' control fits support width');
  for(const text of button.text){
   assert.ok(text.left>=button.box.left+11&&text.right<=button.box.right-11,label+' actual glyphs clear both painted edges');
   assert.ok(text.top>=button.box.top&&text.bottom<=button.box.bottom,label+' wrapped glyphs fit control height');
   assert.ok((button.direction==='rtl'?button.icon.left-text.right:text.left-button.icon.right)>=6,label+' icon and glyphs remain separated');
  }
 }
 const [a,b]=geometry.buttons.map(button=>button.box);
 assert.ok(Math.max(b.left-a.right,a.left-b.right,b.top-a.bottom,a.top-b.bottom)>=8,label+' distinct horizontal or wrapped vertical action gap');
 return {label,updateSupport:geometry};
}