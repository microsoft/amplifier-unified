// Test-only mount of the real editor/settings components. No replacement editor,
// Return handlers, host state adapter or ProseMirror private-field access.
import React,{useId,useLayoutEffect,useRef,useState} from 'react';
import {createRoot} from 'react-dom/client';
import {MarkdownComposer} from '../../src/markdown-composer.jsx';
import {ComposerReturnHelp,ComposerReturnSettings} from '../../src/composer-return-preference.jsx';
import '../../src/reference.css';
import '../../src/appearance-settings.css';

const initial=window.composerFixtureInitial||{};
function App(){
 const returnHelpId=useId();
 const [draft,setDraft]=useState({text:initial.text||'',document:undefined});
 const [props,setProps]=useState({disabled:false,readOnly:false});
 const editorRef=useRef(null),quoteApi=useRef(null),firstEditor=useRef(null);
 const current=useRef(null),record=useRef({changes:0,sends:[],files:[]});
 current.current=draft;
 useLayoutEffect(()=>{
  firstEditor.current=editorRef.current;
  const path=node=>{
   const result=[];
   while(node&&node!==editorRef.current){
    const parent=node.parentNode;if(!parent)return null;
    result.unshift([...parent.childNodes].indexOf(node));node=parent;
   }
   return node?result:null;
  };
  window.composerFixture={
   setProps,
   snapshot(){
    const captured=quoteApi.current.capture(),selection=getSelection();
    return {
     draft:current.current.text,document:captured.doc.toJSON(),caret:captured.pos,
     selection:{anchorPath:path(selection?.anchorNode),anchorOffset:selection?.anchorOffset,
      focusPath:path(selection?.focusNode),focusOffset:selection?.focusOffset},
     sameEditor:editorRef.current===firstEditor.current,
     changes:record.current.changes,sends:structuredClone(record.current.sends),
     files:structuredClone(record.current.files),
    };
   },
  };
  return()=>{delete window.composerFixture;};
 },[]);
 return <>
  <form aria-label="Mounted composer fixture" onSubmit={event=>event.preventDefault()}>
   <MarkdownComposer editorRef={editorRef} quoteApi={quoteApi} returnHelpId={returnHelpId}
    value={draft.text} document={draft.document} disabled={props.disabled} readOnly={props.readOnly}
    onChange={(text,document)=>{record.current.changes++;setDraft({text,document});}}
    onSend={()=>record.current.sends.push(current.current.text)}
    onFiles={files=>record.current.files.push(...files.map(file=>file.name))}
   />
   <ComposerReturnHelp id={returnHelpId}/>
  </form>
  <ComposerReturnSettings/>
 </>;
}
createRoot(document.getElementById('fixture')).render(<App/>);