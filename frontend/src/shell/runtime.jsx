import React,{useCallback,useEffect,useRef,useState} from 'react';
import {request} from '../api';
import {WorkspaceManager,ConversationList} from './navigation-components';
import './shell.css';

const builtins={'builtin.workspaces':WorkspaceManager,'builtin.chats':ConversationList};
const defaultComposition={instances:[{id:'workspaces',package:'builtin.workspaces',slot:'navigation',hideWhen:{instanceId:'chats',navChatScope:'all'}},{id:'chats',package:'builtin.chats',slot:'navigation'}],presentation:{}};
const empty=Object.freeze({view:{},workspaces:[],chatNavigation:{items:[],total:0,pages:1,scope:{}},workspaceExplorer:{rows:[]},library:{bounded:true},sharedHistory:{}});
const recovery=typeof location!=='undefined'&&new URLSearchParams(location.search).get('shell')==='recovery';
function freeze(value){if(value&&typeof value==='object'&&!Object.isFrozen(value)){Object.values(value).forEach(freeze);Object.freeze(value)}return value}

export function useShell(state,dispatch,clientId){
 const [data,setData]=useState(null),[error,setError]=useState('');
 const latest=useRef({data:null,dispatch}),hosts=useRef(new Map()),inflight=useRef(null),again=useRef(false);
 latest.current.dispatch=dispatch;latest.current.state=state;
 const refresh=useCallback(()=>{
  if(inflight.current){again.current=true;return inflight.current}
  inflight.current=request('/api/shell?clientId='+encodeURIComponent(clientId)+(recovery?'&recovery=1':'')).then(next=>{
   latest.current.data=next;setData(next);setError('');
   for(const [id,host] of hosts.current){
    const raw=next.snapshots?.[id]||empty;
    const snapshot={...raw,view:{...raw.view,...Object.assign({},...host.pending.map(item=>item.patch))}};
    if(JSON.stringify(snapshot)!==JSON.stringify(host.snapshot)){
     host.snapshot=freeze(snapshot);host.listeners.forEach(fn=>fn());
    }
   }
  }).catch(e=>setError(e.message)).finally(()=>{inflight.current=null;if(again.current){again.current=false;refresh()}});
  return inflight.current;
 },[clientId]);
 useEffect(()=>{if(state)refresh()},[state?.revision,refresh]);
 useEffect(()=>{
  const onShell=e=>{if(e.detail.shellClientId===clientId)refresh()};
  window.addEventListener('amplifier-shell',onShell);
  return()=>window.removeEventListener('amplifier-shell',onShell);
 },[clientId,refresh]);
 const hostFor=useCallback(instance=>{
  if(!hosts.current.has(instance.id)){
   const record={listeners:new Set(),pending:[],snapshot:freeze(latest.current.data?.snapshots?.[instance.id]||empty)};
   let queue=Promise.resolve();
   const run=(action,args)=>{
    const pending=action==='shell.view.update'?{patch:args.patch}:null;
    if(pending){record.pending.push(pending);record.snapshot=freeze({...record.snapshot,view:{...record.snapshot.view,...pending.patch}});record.listeners.forEach(fn=>fn())}
    const execute=async()=>{
     try{
      const result=await latest.current.dispatch(action,args);
      if(action==='shell.command'&&args.action==='session.select'&&!latest.current.state?.view?.navPinned)await latest.current.dispatch('view.update',{patch:{navExpanded:false}});
      return result;
     }finally{if(pending)record.pending=record.pending.filter(item=>item!==pending);await refresh()}
    };
    const promise=queue.then(execute,execute);queue=promise.catch(()=>{});return promise;
   };
   record.api=Object.freeze({apiVersion:'1.0',clientId,instanceId:instance.id,
    getSnapshot:()=>record.snapshot,subscribe:fn=>{record.listeners.add(fn);return()=>record.listeners.delete(fn)},
    setDirty:dirty=>run('shell.view.update',{clientId,instanceId:instance.id,patch:{},dirty}),
    dispatch:(action,args={})=>action==='view.update'
     ?run('shell.view.update',{clientId,instanceId:instance.id,patch:args.patch||{}})
     :run('shell.command',{clientId,instanceId:instance.id,action,args}),
   });
   hosts.current.set(instance.id,record);
  }
  return hosts.current.get(instance.id).api;
 },[clientId,refresh]);
 const composition=recovery?defaultComposition:data?.effectiveComposition||defaultComposition;
 const recover=useCallback(async target=>{
  await dispatch('shell.recover',{clientId,target,expectedRevision:latest.current.data?.revision||0});
  await refresh();
  if(recovery)location.href=location.pathname;
 },[clientId,dispatch,refresh]);
 const setPresentation=useCallback(async patch=>{
  if(!latest.current.data)await refresh();
  const current=latest.current.data;
  if(!current)throw Error('Shell settings are not available yet.');
  const composition={...current.effectiveComposition,presentation:{...current.effectiveComposition.presentation,...patch}};
  const prepared=await dispatch('shell.changes.prepare',{clientId,expectedRevision:current.revision,composition});
  await dispatch('shell.changes.apply',{clientId,expectedRevision:current.revision,changeId:prepared.result.id});
  await refresh();
 },[clientId,dispatch,refresh]);
 const report=useCallback((instances,message='')=>{
  const current=latest.current.data;if(!current||recovery)return;
  // Reporting bypasses the user's command queue: a slow validation request
  // must not block evidence of already-rendered content.
  return request('/api/actions',{method:'POST',body:{action:'shell.report',args:{clientId,revision:current.revision,previewId:current.preview?.id||null,instances,message}}}).catch(()=>{});
 },[clientId]);
 return {data,error,composition,hostFor,recover,report,setPresentation,ready:!!data,recovery,clientId};
}

class ModuleBoundary extends React.Component{
 state={error:null};
 static getDerivedStateFromError(error){return {error}}
 componentDidCatch(error){this.props.report('error',error.message)}
 render(){return this.state.error?<p role="alert">This component could not render. {this.state.error.message}</p>:this.props.children}
}
function Ready({View,host,report}){
 useEffect(()=>{report('ready');return()=>report('loading')},[View,report]);
 return <View host={host}/>;
}
function ModuleInstance({instance,metadata,host,report}){
 const [loaded,setLoaded]=useState(null),[error,setError]=useState('');
 const builtin=builtins[instance.package];
 useEffect(()=>{
  let alive=true;setError('');
  if(builtin)return;
  if(!metadata?.url){setError(metadata?.error||'Package is unavailable.');report('error');return}
  report('loading');
  import(/* @vite-ignore */ metadata.url).then(module=>{
   const View=module.default({React});if(typeof View!=='function')throw Error('Invalid module factory.');
   if(alive)setLoaded({package:instance.package,View});
  }).catch(error=>{if(alive){setError(error.message);report('error',error.message)}});
  return()=>{alive=false};
 },[instance.package,metadata?.url,metadata?.error,builtin,report]);
 const View=builtin||(loaded?.package===instance.package?loaded.View:null);
 if(error)return <p role="alert">Component unavailable: {error}</p>;
 if(!View)return <p role="status">Loading component…</p>;
 return <ModuleBoundary key={instance.package} report={report}><Ready View={View} host={host} report={report}/></ModuleBoundary>;
}
export function ShellModules({shell}){
 const statuses=useRef({}),callbacks=useRef(new Map()),active=useRef([]),timer=useRef(null);
 active.current=shell.composition.instances.map(item=>item.id);
 const send=useRef(shell.report);send.current=shell.report;
 const reportFor=id=>{
  if(!callbacks.current.has(id))callbacks.current.set(id,(status,message)=>{
   statuses.current[id]=status;clearTimeout(timer.current);
   timer.current=setTimeout(()=>send.current(Object.fromEntries(active.current.map(id=>[id,statuses.current[id]||'loading'])),message),50);
  });
  return callbacks.current.get(id);
 };
 useEffect(()=>{
  clearTimeout(timer.current);
  timer.current=setTimeout(()=>send.current(Object.fromEntries(active.current.map(id=>[id,statuses.current[id]||'loading']))),80);
  return()=>clearTimeout(timer.current);
 },[shell.data?.revision,shell.data?.preview?.id]);
 if(!shell.ready)return <p role="status">Loading navigation…</p>;
 return <>
  {(shell.error||shell.recovery)&&<p role="alert">{shell.recovery?'Recovery mode: optional components are disabled.':shell.error}</p>}
  <div className="a-shell-modules">{shell.composition.instances.map((instance,index)=><section className="a-shell-instance" data-shell-instance={instance.id} data-shell-package={instance.package} hidden={!!instance.hideWhen&&!shell.data?.snapshots?.[instance.id]?.view?.workspaceDraft?.mode&&shell.data?.snapshots?.[instance.hideWhen.instanceId]?.view?.navChatScope===instance.hideWhen.navChatScope} style={{order:index}} key={instance.id}>
   <ModuleInstance instance={instance} metadata={shell.data?.packages?.[instance.package]} host={shell.hostFor(instance)} report={reportFor(instance.id)}/>
  </section>)}</div>
  <details className="a-shell-recovery"><summary>Shell controls</summary><button type="button" onClick={()=>shell.recover('lastGood')}>Restore last working shell</button><button type="button" onClick={()=>shell.recover('default')}>Restore default shell</button><a href="?shell=recovery">Open recovery mode</a></details>
 </>;
}
