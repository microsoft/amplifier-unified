import React,{useEffect,useLayoutEffect,useRef,useState} from 'react';
import {ArrowLeft,ArrowUp,ArrowDown,ChevronRight,GripVertical} from 'lucide-react';
import './settings-collections.css';

export function CollectionRow({id,label,description,selected,onSelect,checked,onCheck,disabled,badge,checkLabel,checkAction='view.update'}){
 return <div className={'a-collection-row'+(selected?' selected':'')} data-collection-id={id}>
  {onCheck&&<label className="a-collection-check"><input type="checkbox" aria-label={checkLabel||'Select '+label} checked={!!checked} disabled={disabled} data-action={checkAction} onChange={e=>onCheck(e.target.checked)}/></label>}
  <button type="button" aria-current={selected?'true':undefined} data-action="view.update" onClick={onSelect}><span><strong>{label}</strong>{description&&<small>{description}</small>}</span>{badge&&<small className="a-collection-badge">{badge}</small>}<ChevronRight aria-hidden="true"/></button>
 </div>;
}
export function Collection({list,children,detailOpen=true,onBack,label='items'}){
 const listRef=useRef(null),detailRef=useRef(null);
 useEffect(()=>{if(detailOpen&&typeof window!=='undefined'&&window.matchMedia('(max-width:959px)').matches)detailRef.current?.focus({preventScroll:true});},[detailOpen]);
 const back=()=>{onBack?.();requestAnimationFrame(()=>listRef.current?.querySelector('button[aria-current=true],button')?.focus({preventScroll:true}));};
 return <div className={'a-collection'+(detailOpen?' detail-open':'')}><div className="a-collection-list" ref={listRef}>{list}</div><div className="a-collection-detail" ref={detailRef} tabIndex={-1}>
 {onBack&&<button type="button" className="a-link a-collection-back" data-action="view.update" onClick={back}><ArrowLeft/>Back to {label}</button>}{children}</div></div>;
}
export function moveItem(ids,id,to){const next=ids.filter(key=>key!==id);next.splice(Math.max(0,Math.min(to,next.length)),0,id);return next;}

// Preview is local until drop; the enclosing editor owns the draft and Save/Cancel.
export function OrderEditor({title,description,items,ids,onChange,onSave,onCancel,busy=false,error,action='view.update'}){
 const root=useRef(null),drag=useRef(null),rects=useRef(new Map()),[preview,setPreview]=useState(null),[floating,setFloating]=useState(null),[announcement,announce]=useState('');
 const shown=preview||ids,rows=new Map(items.map(item=>[item.id,item]));
 const focus=id=>requestAnimationFrame(()=>root.current?.querySelector(`[data-order-id="${CSS.escape(id)}"] .a-order-grip`)?.focus({preventScroll:true}));
 const capture=()=>{rects.current=new Map([...root.current.querySelectorAll('[data-order-id]')].map(node=>[node.dataset.orderId,node.getBoundingClientRect()]));};
 useLayoutEffect(()=>{
  if(!root.current)return;
  if(!window.matchMedia('(prefers-reduced-motion: reduce)').matches)for(const node of root.current.querySelectorAll('[data-order-id]')){
   const before=rects.current.get(node.dataset.orderId);
   node.getAnimations().forEach(animation=>animation.cancel());
   const after=node.getBoundingClientRect();
   if(before&&node.dataset.orderId!==drag.current?.id&&before.top!==after.top)node.animate([{transform:`translateY(${before.top-after.top}px)`},{transform:'translateY(0)'}],{duration:150,easing:'ease-out'});
  }
 },[shown.join('\0')]);
 const finish=cancel=>{
  const current=drag.current;if(!current)return;capture();drag.current=null;
  if(current.active&&!cancel&&current.valid){onChange(current.preview);announce(`${rows.get(current.id)?.label} moved to position ${current.preview.indexOf(current.id)+1}. Save order to apply.`);}else announce('Reorder cancelled.');
  setPreview(null);setFloating(null);if(root.current?.hasPointerCapture(current.pointerId))root.current.releasePointerCapture(current.pointerId);focus(current.id);
 };
 useEffect(()=>{const key=e=>{if(e.key==='Escape'&&drag.current){e.preventDefault();e.stopPropagation();finish(true);}};document.addEventListener('keydown',key,true);return()=>document.removeEventListener('keydown',key,true);});
 const down=(event,id)=>{
  if(busy||event.button!==0)return;event.preventDefault();
  const node=event.currentTarget.closest('[data-order-id]'),box=node.getBoundingClientRect(),bounds=root.current.querySelector('.a-order-list').getBoundingClientRect();
  const nodes=[...root.current.querySelectorAll('[data-order-id]')],scroll=root.current.closest('.a-settings-content');
  drag.current={id,pointerId:event.pointerId,x:event.clientX,y:event.clientY,box,bounds,scroll,scrollTop:scroll?.scrollTop||0,centers:nodes.map(n=>{const r=n.getBoundingClientRect();return(r.top+r.bottom)/2}),preview:[...ids],active:false,valid:true};
  root.current.setPointerCapture(event.pointerId);
 };
 const moving=event=>{
  const current=drag.current;if(!current)return;
  if(!current.active&&Math.hypot(event.clientX-current.x,event.clientY-current.y)<5)return;
  current.active=true;event.preventDefault();
  const {box,bounds,scroll}=current;
  if(scroll){const area=scroll.getBoundingClientRect();if(event.clientY<area.top+35)scroll.scrollTop-=12;else if(event.clientY>area.bottom-35)scroll.scrollTop+=12;}
  const delta=(scroll?.scrollTop||0)-current.scrollTop;
  current.valid=event.clientX>=bounds.left-24&&event.clientX<=bounds.right+24&&event.clientY>=bounds.top-delta-28&&event.clientY<=bounds.bottom-delta+28;
  let to=0,best=Infinity;current.centers.forEach((y,index)=>{const distance=Math.abs(event.clientY+delta-y);if(distance<best){best=distance;to=index;}});
  const next=moveItem(ids,current.id,to);capture();current.preview=next;setPreview(next);
  setFloating({id:current.id,left:box.left+8,top:event.clientY-(current.y-box.top),width:box.width,valid:current.valid});
 };
 return <section className="a-order-editor" ref={root} onPointerMove={moving} onPointerUp={()=>finish(false)} onPointerCancel={()=>finish(true)} onLostPointerCapture={()=>{if(drag.current)finish(true);}} onDragStart={e=>e.preventDefault()}>
  <h4>{title}</h4><p>{description}</p><p className="a-caption">Drag the handle or choose a position. Changes apply when you save.</p>
  <div className="a-order-list">{shown.map((id,index)=>{const item=rows.get(id)||{label:id};return <div key={id} data-order-id={id} className={'a-order-item'+(floating?.id===id?' a-order-placeholder':'')}>
   {floating?.id===id&&floating.valid&&<span className="a-order-insertion" aria-hidden="true"/>}
   <button type="button" className="a-icon a-order-grip" disabled={busy} draggable={false} data-action="view.update" aria-label={'Reorder '+item.label} title="Drag, or press the up and down arrow keys" onPointerDown={e=>down(e,id)} onKeyDown={e=>{if(['ArrowUp','ArrowDown'].includes(e.key)){e.preventDefault();capture();const next=moveItem(ids,id,index+(e.key==='ArrowUp'?-1:1));onChange(next);announce(`${item.label}, position ${next.indexOf(id)+1}`);focus(id);}}}><GripVertical/></button>
   <span className="a-order-copy"><strong>{item.label}</strong><small>{item.description}</small></span>
   <span className="a-order-arrows"><button type="button" className="a-icon" disabled={busy||index===0} aria-label={'Move '+item.label+' up'} data-action="view.update" onClick={()=>{capture();onChange(moveItem(ids,id,index-1));}}><ArrowUp/></button><button type="button" className="a-icon" disabled={busy||index===shown.length-1} aria-label={'Move '+item.label+' down'} data-action="view.update" onClick={()=>{capture();onChange(moveItem(ids,id,index+1));}}><ArrowDown/></button></span>
   <select aria-label={'Position of '+item.label} value={index} disabled={busy} data-action="view.update" onChange={e=>{capture();onChange(moveItem(ids,id,Number(e.target.value)));announce(`${item.label}, position ${Number(e.target.value)+1}`);}}>{shown.map((_,i)=><option key={i} value={i}>{i+1}</option>)}</select>
  </div>;})}</div>
  {floating&&<div className="a-order-floating" aria-hidden="true" style={{left:floating.left,top:floating.top,width:floating.width}}><GripVertical/><strong>{rows.get(floating.id)?.label}</strong><small>{rows.get(floating.id)?.description}</small></div>}
  <span className="a-sr-only" role="status" aria-live="polite">{announcement}</span>{error&&<p role="alert" className="a-danger">{error}</p>}
  <div className="a-dialog-actions"><button type="button" className="a-primary" disabled={busy||!!floating} data-action={action} onClick={onSave}>{busy?'Saving order…':'Save order'}</button><button type="button" className="a-soft" disabled={busy} data-action="view.update" onClick={onCancel}>Cancel</button></div>
 </section>;
}
