import React from 'react';
import {createRoot} from 'react-dom/client';
import {flushSync} from 'react-dom';

// This page is served by the host validator on a sterile loopback origin. It
// has no app state, credential, service transport, or live command interface.
window.validateShellModule=async()=>{
 const listeners=new Set(),commands=[];
 let snapshot={view:{},selectedWorkspaceId:null,selectedSessionId:null,workspaces:[],chatNavigation:{items:[],total:0,index:0,pages:1,scope:{}},workspaceExplorer:{rows:[],page:1,pages:1,filter:''},sharedHistory:{},library:{bounded:true},attention:{sessions:{}}};
 const host=Object.freeze({apiVersion:'1.0',clientId:'validation-client',instanceId:'validation-instance',getSnapshot:()=>snapshot,subscribe:fn=>{listeners.add(fn);return()=>listeners.delete(fn)},dispatch:async(action,args={})=>{commands.push({action,args});return {accepted:true}},setDirty:async()=>({accepted:true})});
 const moduleUrl='/module.mjs';
 const factory=(await import(/* @vite-ignore */ moduleUrl)).default;
 if(typeof factory!=='function')throw Error('Export a default component factory.');
 const Component=factory({React});
 if(typeof Component!=='function')throw Error('The factory must return a React component.');
 for(let iteration=0;iteration<2;iteration++){
  const root=createRoot(document.getElementById('root'));
  flushSync(()=>root.render(<Component host={host}/>));
  await new Promise(resolve=>setTimeout(resolve,30));
  snapshot={...snapshot,selectedWorkspaceId:'fixture-project',workspaces:[{id:'fixture-project',name:'Fixture',path:'/fixture/project',available:true}],chatNavigation:{...snapshot.chatNavigation,total:1,items:[{id:'fixture-chat',title:'Fixture chat',workspace:'/fixture/project',workspaceId:'fixture-project',status:'idle',pinned:false}]}};
  flushSync(()=>listeners.forEach(listener=>listener()));
  await new Promise(resolve=>setTimeout(resolve,30));
  if(!document.getElementById('root').textContent.trim())throw Error('Module did not render fixture content.');
  flushSync(()=>root.unmount());
  if(listeners.size)throw Error('Module leaked a host subscription after unmount.');
 }
 return {status:'passed',checks:['closed-import-graph','shared-react-factory','empty-and-populated-navigation','mount-update-unmount-remount','subscription-cleanup'],fixtureCommands:commands.length};
};
