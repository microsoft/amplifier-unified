import React,{useEffect,useId,useRef,useState} from 'react';
import {ArrowLeft,ArrowRight,History,Home,MessageCircle,Folder,Search,Check} from 'lucide-react';

// One menu for all visits. Pointer hold is an optional mouse/pen shortcut; the
// always-visible opener and keyboard path do not depend on hover or long touch.
export function DestinationHistory({history,onVisit,disabled=false}){
 const [open,setOpen]=useState(false),root=useRef(null),opener=useRef(null),focusReturn=useRef(null),hold=useRef(null),suppressClick=useRef(false),menuId=useId();
 const busyCycle=useRef(0);
 const rows=history?.entries||[],busy=disabled||!history?.ready||history?.pending;
 const cancelHold=()=>{if(hold.current)clearTimeout(hold.current.timer);hold.current=null};
 const close=(restore=false)=>{cancelHold();setOpen(false);if(restore)opener.current?.focus()};
 const show=button=>{cancelHold();opener.current=button;setOpen(true)};
 useEffect(()=>()=>cancelHold(),[]);
 useEffect(()=>{
  if(busy){
   busyCycle.current++;
   if(root.current?.contains(document.activeElement))focusReturn.current=opener.current||document.activeElement;
   close();
  }else if(focusReturn.current){
   // Never steal focus if the person moved elsewhere while the request ran.
   if(document.activeElement===document.body){
    const target=focusReturn.current;
    if(target?.isConnected&&!target.disabled)target.focus();
    else root.current?.querySelector('[aria-label="Navigation history"]')?.focus();
   }
   focusReturn.current=null;
  }
 },[busy]);
 useEffect(()=>{
  if(!open)return;
  const target=root.current?.querySelector('[role="menuitem"]:not(:disabled)')||root.current?.querySelector('[role="menu"]');
  target?.focus();
  const dismiss=event=>{if(!root.current?.contains(event.target))close()};
  document.addEventListener('pointerdown',dismiss);
  return()=>document.removeEventListener('pointerdown',dismiss);
 },[open]);
 const visit=key=>{
  if(!key||busy)return;
  const cycle=busyCycle.current,target=opener.current;
  focusReturn.current=target;close(true);
  const settled=()=>{if(busyCycle.current===cycle&&focusReturn.current===target)focusReturn.current=null};
  Promise.resolve(onVisit(key)).then(settled,settled);
 };
 const keyDown=event=>{
  if(event.key==='Escape'&&open){event.preventDefault();event.stopPropagation();close(true);return}
  if(!['ArrowDown','ArrowUp','Home','End'].includes(event.key))return;
  if(!open){if(event.key==='ArrowDown'&&!busy&&rows.length){event.preventDefault();show(event.currentTarget.querySelector(':focus')||document.activeElement)}return}
  const items=[...root.current.querySelectorAll('[role="menuitem"]:not(:disabled)')];if(!items.length)return;
  event.preventDefault();const index=items.indexOf(document.activeElement);
  const next=event.key==='Home'?0:event.key==='End'?items.length-1:event.key==='ArrowDown'?(index+1)%items.length:(index-1+items.length)%items.length;
  items[next].focus();
 };
 const startHold=event=>{
  suppressClick.current=false;cancelHold();
  if(event.pointerType==='touch'||event.button!==0||busy)return;
  const button=event.currentTarget;
  hold.current={x:event.clientX,y:event.clientY,timer:setTimeout(()=>{suppressClick.current=true;show(button)},450)};
 };
 const arrow=(direction,key,Icon)=><button type="button" className="a-icon" aria-label={direction+' in navigation history'} disabled={busy||!key} onPointerDown={startHold} onPointerUp={cancelHold} onPointerCancel={()=>{cancelHold();suppressClick.current=false}} onPointerLeave={cancelHold} onPointerMove={event=>{if(hold.current&&Math.hypot(event.clientX-hold.current.x,event.clientY-hold.current.y)>8)cancelHold()}} onClick={event=>{if(suppressClick.current){suppressClick.current=false;event.preventDefault();return}opener.current=event.currentTarget;visit(key)}}><Icon/></button>;
 const duplicates=label=>rows.filter(row=>row.label===label).length>1;
 return <div className="a-destination-history" ref={root} role="group" aria-label="Navigate visited chats and workspaces" onKeyDown={keyDown} onBlur={event=>{if(event.relatedTarget&&!event.currentTarget.contains(event.relatedTarget))close()}}>
  {arrow('Back',history?.backKey,ArrowLeft)}{arrow('Forward',history?.forwardKey,ArrowRight)}
  <button type="button" className="a-icon" aria-label="Navigation history" aria-haspopup="menu" aria-expanded={open} aria-controls={open?menuId:undefined} disabled={busy||!rows.length} onClick={event=>open?close(true):show(event.currentTarget)}><History/></button>
  {open&&<div className="a-destination-menu" data-private="true">
   {history.memoryOnly&&<p role="status">History is kept only while this page stays open.</p>}
   <div id={menuId} role="menu" aria-label="Visited destinations" tabIndex={-1} data-private="true">
   {rows.map((row,index)=>{
    const current=index===history.cursor&&!history.detached,Icon=row.kind==='home'?Home:row.kind==='chat'?MessageCircle:row.kind==='chats'?Search:Folder;
    const detail=[row.kind==='chat'?row.workspaceLabel:row.kind==='workspace'?row.tab:'',duplicates(row.label)&&row.id?row.id:''].filter(Boolean).join(' · ');
    return <button type="button" role="menuitem" key={row.key} data-visit-key={row.key} data-view-label="Private navigation destination" aria-current={current?'page':undefined} disabled={current} onClick={()=>visit(row.key)}><Icon/><span><strong>{row.label}</strong>{detail&&<small>{detail}</small>}</span>{current&&<Check/>}</button>;
   })}
   </div>
  </div>}
 </div>;
}
