import {useCallback,useEffect,useRef,useState} from 'react';

// Editor text is private to this tab and is not a presentation preference.
// Only this production editor's private drafts are restored. Old editor and
// trial records remain stored, inert and undeleted.
export const appearanceEditorKey='amplifier.appearance-editor.v3';
export function appearanceEditorStorageKey(namespace){
 return /^amplifier\.appearance\.v2:[a-f0-9]{32}$/.test(namespace||'')?appearanceEditorKey+':'+namespace:null;
}
function valid(value){
 return value&&typeof value.name==='string'&&value.name.length<=100&&typeof value.css==='string'&&value.css.length<=1000000&&typeof value.description==='string'&&value.description.length<=240&&typeof value.dirty==='boolean'&&(
  value.entry===null||value.entry&&typeof value.entry.id==='string'&&typeof value.entry.name==='string'&&['saved','built-in'].includes(value.entry.source)&&/^[a-f0-9]{64}$/.test(value.entry.fingerprint||''));
}
function blank(theme,fallback){
 return {entry:null,name:theme?.name||'New appearance',css:typeof theme?.css==='string'?theme.css:(fallback||''),description:'',dirty:false};
}
function load(storage,key,theme,fallback){
 if(!key)return blank(theme,fallback);
 try{
  const record=JSON.parse(storage()?.getItem(key)||'null');
  if(record?.version===3&&valid(record.draft))return record.draft;
 }catch{}
 return blank(theme,fallback);
}
const browserStorage=()=>globalThis.sessionStorage;
export function useAppearanceEditor(theme,fallback,{storage=browserStorage,namespace}={}){
 const key=appearanceEditorStorageKey(namespace);
 const [draft,setState]=useState(()=>load(storage,key,theme,fallback));
 const current=useRef({key,draft});
 const context=useRef({theme,fallback});context.current={theme,fallback};
 const [storageError,setStorageError]=useState('');
 const setDraft=useCallback(update=>{
  const base=current.current.key===key?current.current.draft:load(storage,key,context.current.theme,context.current.fallback);
  const next=typeof update==='function'?update(base):update;
  if(next===base)return;
  // Entries need identity/metadata only. The editable CSS has one canonical copy.
  const {css:entryCss,...entry}=next.entry||{};
  const value={...next,entry:next.entry?entry:null};
  current.current={key,draft:value};setState(value);
  try{
   if(!key)throw Error('Appearance owner unavailable');
   const target=storage();if(!target)throw Error('Browser storage unavailable');
   target.setItem(key,JSON.stringify({version:3,draft:value}));setStorageError('');
  }catch{setStorageError('Your appearance draft stays in this tab, but could not be saved for reload. Keep this tab open.')}
 },[key,storage]);
 // Each published object is a revision token for the entire draft, including
 // its target/fingerprint. Compare inside the persistent owner, not a mounted
 // Settings closure. Even editing away and back invalidates an older token.
 const compareAndSetDraft=useCallback((expected,update)=>{
  if(current.current.key!==key||current.current.draft!==expected)return false;
  setDraft(update);return true;
 },[key,setDraft]);
 useEffect(()=>{
  if(current.current.key!==key){
   const next=load(storage,key,theme,fallback);
   current.current={key,draft:next};setState(next);setStorageError('');
  }
 },[fallback,key,storage,theme]);
 useEffect(()=>{
  if(!theme||current.current.key!==key||current.current.draft.entry||current.current.draft.dirty)return;
  const previous=current.current.draft;
  const next={...previous,name:theme.name||'New appearance',css:typeof theme.css==='string'?theme.css:(fallback||'')};
  if(previous.name===next.name&&previous.css===next.css)return;
  // Reflect the committed appearance without turning page entry/reload into a
  // private draft write. Only explicit editor changes are persisted.
  current.current={key,draft:next};setState(next);
 },[fallback,key,theme?.name,theme?.css]);
 const visibleDraft=current.current.key===key?current.current.draft:load(storage,key,theme,fallback);
 return {draft:visibleDraft,setDraft,compareAndSetDraft,storageError};
}