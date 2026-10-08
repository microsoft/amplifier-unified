import React,{useLayoutEffect,useRef,useState} from 'react';
import {createPortal} from 'react-dom';
import {toggleMark} from 'prosemirror-commands';
import {Link,Bold,Italic} from 'lucide-react';
import 'prosemirror-view/style/prosemirror.css';
import {EditorView} from 'prosemirror-view';
import {composerState,composerMarkdown,composerNewline,pasteMarkdown,composerClipboard,composerFormat,formatComposerBlock,setComposerLink} from './markdown-composer.js';
import {clipboardPaste} from './clipboard-paste';
import {resizeComposer} from './composer';
import './composer-formatting.css';

export function MarkdownComposer({value,onChange,onSend,onFiles,disabled=false,readOnly=false,pasteDisabled=false,editorRef}){
 const host=useRef(null),editor=useRef(null),latest=useRef(null),published=useRef(value);
 const toolbar=useRef(null),refresh=useRef(()=>{}),dismissed=useRef(null);
 const [selection,setSelection]=useState(null),[linkOpen,setLinkOpen]=useState(false),[link,setLink]=useState(''),[linkError,setLinkError]=useState('');
 const openLink=()=>{
  const view=editor.current;if(!view||view.state.selection.empty)return;
  let href='';view.state.doc.nodesBetween(view.state.selection.from,view.state.selection.to,node=>{const mark=node.marks.find(mark=>mark.type.name==='link');if(mark)href=mark.attrs.href;});
  setLink(href);setLinkError('');setLinkOpen(true);
 };
 latest.current={value,onChange,onSend,onFiles,disabled,readOnly,pasteDisabled};
 useLayoutEffect(()=>{
  const editable=()=>!latest.current.disabled&&!latest.current.readOnly;
  const view=new EditorView(host.current,{
   state:composerState(latest.current.value),editable,clipboardTextSerializer:composerClipboard,
   attributes:{role:'textbox','aria-label':'Message Amplifier','aria-multiline':'true','data-action':'view.update',class:'a-markdown-composer',spellcheck:'true'},
   dispatchTransaction(transaction){
    view.updateState(view.state.apply(transaction));
    if(transaction.docChanged){published.current=composerMarkdown(view.state.doc);latest.current.onChange(published.current);}
    view.dom.dataset.empty=String(view.state.doc.childCount===1&&view.state.doc.firstChild.type.name==='paragraph'&&view.state.doc.firstChild.content.size===0);
    resizeComposer(view.dom);
    refresh.current();
   },
   handleKeyDown(view,event){
    if(!editable()||view.composing||event.isComposing||event.keyCode===229)return false;
    if(event.key==='Escape'&&!view.state.selection.empty){dismissed.current=view.state.selection.from+':'+view.state.selection.to;setSelection(null);setLinkOpen(false);return true;}
    if((event.metaKey||event.ctrlKey)&&event.key.toLowerCase()==='k'&&!view.state.selection.empty){event.preventDefault();openLink();return true;}
    if(event.key==='Enter'){
     event.preventDefault();
     if(event.shiftKey||event.ctrlKey)composerNewline(view.state,view.dispatch,view);
     else latest.current.onSend();
     return true;
    }
    return false;
   },
   handleDOMEvents:{
    beforeinput(view,event){
     // Virtual keyboards may emit beforeinput without a usable keydown.
     if(editable()&&!view.composing&&!event.isComposing&&event.inputType==='insertParagraph'){
      event.preventDefault();latest.current.onSend();return true;
     }
     return false;
    },
    paste(view,event){
     if(!editable()||latest.current.pasteDisabled){event.preventDefault();return true;}
     const paste=clipboardPaste(event.clipboardData);
     event.preventDefault();
     if(paste.text)pasteMarkdown(view,paste.text);
     if(paste.files.length)latest.current.onFiles(paste.files);
     return true;
    },
    drop(view,event){
     if(!editable()||latest.current.pasteDisabled){event.preventDefault();return true;}
     if(event.dataTransfer?.files.length){event.preventDefault();event.stopPropagation();latest.current.onFiles([...event.dataTransfer.files]);return true;}
     // External text drops follow the same plain-text/Markdown policy as paste.
     if(!view.dragging&&event.dataTransfer?.getData('text/plain')){event.preventDefault();pasteMarkdown(view,event.dataTransfer.getData('text/plain'));return true;}
     return false;
    },
   },
  });
  editor.current=view;editorRef.current=view.dom;
  let frame;
  const update=()=>{
   const {from,to,empty}=view.state.selection,key=from+':'+to;
   const focused=view.hasFocus()||toolbar.current?.contains(document.activeElement);
   if(empty||!editable()||!focused||dismissed.current===key){setSelection(null);if(empty||!focused)setLinkOpen(false);return;}
   const a=view.coordsAtPos(from),b=view.coordsAtPos(to),bounds=view.dom.getBoundingClientRect();
   if(b.bottom<bounds.top||a.top>bounds.bottom){setSelection(null);return;}
   setSelection({...composerFormat(view.state),left:Math.max(8,Math.min(a.left,window.innerWidth-Math.min(330,window.innerWidth-16)-8)),top:Math.max(8,Math.min(Math.max(a.top,bounds.top)-52,window.innerHeight-160))});
  };
  const schedule=()=>{cancelAnimationFrame(frame);frame=requestAnimationFrame(update);};
  refresh.current=()=>{dismissed.current=null;schedule();};
  document.addEventListener('selectionchange',schedule);document.addEventListener('focusin',schedule);
  window.addEventListener('resize',schedule);window.addEventListener('scroll',schedule,true);
  view.dom.dataset.empty=String(!latest.current.value);
  resizeComposer(view.dom);
  return()=>{cancelAnimationFrame(frame);refresh.current=()=>{};document.removeEventListener('selectionchange',schedule);document.removeEventListener('focusin',schedule);window.removeEventListener('resize',schedule);window.removeEventListener('scroll',schedule,true);view.destroy();editor.current=null;if(editorRef.current===view.dom)editorRef.current=null;};
 },[]);
 useLayoutEffect(()=>{
  const view=editor.current;if(!view)return;
  view.setProps({editable:()=>!disabled&&!readOnly});
  view.dom.setAttribute('aria-disabled',String(disabled));view.dom.setAttribute('aria-readonly',String(readOnly));view.dom.setAttribute('aria-busy',String(readOnly));
  // Do not rebuild selection or undo history for our own autosave echoes.
  if(value!==published.current&&!view.composing){
   published.current=value;view.updateState(composerState(value));
   view.dom.dataset.empty=String(!value);resizeComposer(view.dom);
  }
  refresh.current();
 },[value,disabled,readOnly]);
 const dismiss=()=>{const view=editor.current;dismissed.current=view.state.selection.from+':'+view.state.selection.to;setSelection(null);setLinkOpen(false);view.focus();};
 const mark=name=>{const view=editor.current;toggleMark(view.state.schema.marks[name])(view.state,view.dispatch);view.focus();};
 return <><div className="a-markdown-composer-host" ref={host}/>{selection&&!disabled&&!readOnly&&createPortal(
  <div className="a-composer-format" ref={toolbar} style={{left:selection.left,top:selection.top}} onKeyDown={event=>{if(event.key==='Escape'){event.preventDefault();event.stopPropagation();dismiss();}}}>
   <div role="toolbar" aria-label="Format selected text" className="a-composer-format-row">
    <button type="button" aria-label="Link" title="Link (Ctrl/⌘ K)" aria-pressed={selection.link} onMouseDown={e=>e.preventDefault()} onClick={openLink}><Link size={18}/></button>
    <button type="button" aria-label="Bold" title="Bold (Ctrl/⌘ B)" aria-pressed={selection.bold} onMouseDown={e=>e.preventDefault()} onClick={()=>mark('strong')}><Bold size={18}/></button>
    <button type="button" aria-label="Italic" title="Italic (Ctrl/⌘ I)" aria-pressed={selection.italic} onMouseDown={e=>e.preventDefault()} onClick={()=>mark('em')}><Italic size={18}/></button>
    <select aria-label="Text style" value={selection.block} onChange={e=>formatComposerBlock(editor.current,e.target.value)}>
     <option value="text">Text</option><option value="h1">Heading 1</option><option value="h2">Heading 2</option><option value="h3">Heading 3</option><option value="ordered_list">Numbered list</option><option value="bullet_list">Bulleted list</option>
    </select>
   </div>
   {linkOpen&&<form className="a-composer-link" onSubmit={e=>{e.preventDefault();e.stopPropagation();if(setComposerLink(editor.current,link)){setLinkOpen(false);setLinkError('');}else setLinkError('Use an http, https, mailto, or relative link.');}}>
    <label>Link address<input autoFocus aria-label="Link address" placeholder="https://example.com" value={link} onChange={e=>setLink(e.target.value)}/></label>
    <div><button type="submit">Apply</button><button type="button" onClick={()=>{setComposerLink(editor.current,'');setLinkOpen(false);}}>Remove link</button></div>
    {linkError&&<p role="alert">{linkError}</p>}
   </form>}
  </div>,host.current.closest('#amp-one')||document.body)}</>;
}
