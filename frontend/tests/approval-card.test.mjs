import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {create} from 'react-test-renderer';
import {renderToStaticMarkup} from 'react-dom/server';
import {createServer} from 'vite';
const server=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {ApprovalCard,ApprovalPanel,PermissionHistory,approvalGroups}=await server.ssrLoadModule('/src/approval-card.jsx');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
test.after(()=>server.close());
for(const decision of ['approve','deny'])test(`note accompanies ${decision}, targets the rendered chat and survives failure`,async()=>{
 let root;const calls=[];
 await act(async()=>{root=create(React.createElement(ApprovalCard,{approval:{id:'approval'},sessionId:'original-chat',act:async(...args)=>{calls.push(args);throw Error('Not confirmed')}}))});
 await act(async()=>root.root.findByType('textarea').props.onChange({target:{value:'Use the workspace copy.'}}));
 await act(async()=>root.root.findAllByType('button')[decision==='approve'?0:1].props.onClick());
 assert.deepEqual(calls,[['approval.respond',{sessionId:'original-chat',id:'approval',decision,comment:'Use the workspace copy.'}]]);
 assert.equal(root.root.findByType('textarea').props.value,'Use the workspace copy.');
 assert.match(JSON.stringify(root.toJSON()),/Not confirmed/);
 await act(async()=>root.unmount());
});

test('historical decisions leave chat; pending and unresolved requests stay actionable',()=>{
 const approvals=[
  {id:'capability:allowed',status:'allow',prompt:'Approved scope',comment:'Decision note',actor:'human',source:'fixture'},
  {id:'native-pending',status:'pending',prompt:'Current native request'},
  {id:'capability:expired',status:'expired',prompt:'Unresolved old request'},
  {id:'capability:denied',status:'deny',prompt:'Denied scope'},
  {id:'capability:dismissed',status:'expired',dismissedAt:42,prompt:'Dismissed scope',dismissalComment:'History note'},
  {id:'legacy-pending',prompt:'Legacy current request'}
 ];
 const session={id:'original-chat',approvals},before=JSON.stringify(session),groups=approvalGroups(session);
 assert.deepEqual(groups.pending.map(row=>row.id),['native-pending','legacy-pending']);
 assert.deepEqual(groups.retired.map(row=>row.id),['capability:expired']);
 assert.deepEqual(groups.history.map(row=>row.id),['capability:allowed','capability:denied','capability:dismissed']);
 const chat=renderToStaticMarkup(React.createElement(ApprovalPanel,{session}));
 assert.match(chat,/Permissions history \(3\)/);
 assert.match(chat,/Current native request/);
 assert.match(chat,/Unresolved old request/);
 assert.doesNotMatch(chat,/data-permission-history-id|Approved scope|Decision note|Denied scope|Dismissed scope/);
 const history=renderToStaticMarkup(React.createElement(PermissionHistory,{session}));
 assert.match(history,/Approved scope.*Decision note.*Denied scope.*History note/);
 assert.doesNotMatch(history,/data-approval-id|approval.respond|<button|Unresolved old request|Current native request/);
 assert.equal(JSON.stringify(session),before,'Presentation preserves exact records and their order');
 const historicalOnly=renderToStaticMarkup(React.createElement(ApprovalPanel,{session:{id:session.id,approvals:groups.history}}));
 assert.doesNotMatch(historicalOnly,/data-part="approvals"|<details/);
});

test('history entry uses the rendered chat and retains an unconfirmed opening error',async()=>{
 const calls=[];let root;
 await act(async()=>{root=create(React.createElement(ApprovalPanel,{session:{id:'rendered-chat',approvals:[{id:'capability:done',status:'deny'}]},canvasDraft:{path:'keep-draft.md'},dispatch:async(...args)=>{calls.push(args);return {accepted:false,error:'View changed'}}}))});
 await act(async()=>root.root.findByType('button').props.onClick());
 assert.deepEqual(calls,[['view.update',{sessionId:'rendered-chat',patch:{settingsCanvas:false,canvasControlsExpanded:false,canvasDraft:{path:'keep-draft.md',library:true,open:false,browser:false,delivery:'permissions'}}}]]);
 assert.match(JSON.stringify(root.toJSON()),/View changed/);
 assert.equal(root.root.findByType('button').props.disabled,false);
 await act(async()=>root.unmount());
});
