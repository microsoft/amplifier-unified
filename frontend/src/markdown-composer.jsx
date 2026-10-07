import React,{useLayoutEffect,useRef} from 'react';
import 'prosemirror-view/style/prosemirror.css';
import {EditorView} from 'prosemirror-view';
import {composerState,composerMarkdown,composerNewline,pasteMarkdown,composerClipboard} from './markdown-composer.js';
import {clipboardPaste} from './clipboard-paste';
import {resizeComposer} from './composer';

export function MarkdownComposer({value,onChange,onSend,onFiles,disabled=false,readOnly=false,pasteDisabled=false,editorRef}){
 const host=useRef(null),editor=useRef(null),latest=useRef(null),published=useRef(value);
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
   },
   handleKeyDown(view,event){
    if(!editable()||view.composing||event.isComposing||event.keyCode===229)return false;
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
  view.dom.dataset.empty=String(!latest.current.value);
  resizeComposer(view.dom);
  return()=>{view.destroy();editor.current=null;if(editorRef.current===view.dom)editorRef.current=null;};
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
 },[value,disabled,readOnly]);
 return <div className="a-markdown-composer-host" ref={host}/>;
}
