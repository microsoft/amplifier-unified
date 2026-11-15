// Mounted production card with synthetic canonical state + action transport.
// This fixture never connects to an actual Unified host or native owner.
import React,{useState} from 'react';
import {createRoot} from 'react-dom/client';
import {WorktreeControls} from '../../src/worktrees';
import '../../src/reference.css';

const initial=await (await fetch('/fixture-state')).json();
function Fixture(){
 const [session,setSession]=useState(initial);
 window.publishMoveFixture=setSession;
 const act=async(action,args,options)=>{
  const response=await (await fetch('/api/actions',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action,args,options})})).json();
  if(!response.accepted&&options?.propagateError)throw Error(response.error);
  return response;
 };
 return <WorktreeControls session={session} act={act}/>;
}
createRoot(document.getElementById('fixture')).render(<Fixture/>);