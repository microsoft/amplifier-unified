import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';
const vite=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {CoordinationPanel,RelatedWork}=await vite.ssrLoadModule('/src/coordination.jsx');
const {WorkHeader}=await vite.ssrLoadModule('/src/work-shell.jsx');
const {WorkNavigationContext}=await vite.ssrLoadModule('/src/work-navigation.js');
const {IntegrationEvidence,repositoryChoices}=await vite.ssrLoadModule('/src/integration-evidence.jsx');
const {IntegrationValidation}=await vite.ssrLoadModule('/src/integration-validation.jsx');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
const storage=new Map();globalThis.sessionStorage={getItem:key=>storage.get(key)||null,setItem:(key,value)=>storage.set(key,value),removeItem:key=>storage.delete(key)};
test.beforeEach(()=>storage.clear());test.after(()=>vite.close());
const content=node=>typeof node==='string'?node:(node?.children||[]).map(content).join('');
const button=(root,label)=>root.root.findAllByType('button').find(node=>content(node)===label);
const details=(root,label)=>root.root.findAllByType('details').find(node=>node.findAllByType('summary').some(summary=>content(summary)===label));
const select=(root,id,value)=>act(async()=>root.root.findByProps({id}).props.onChange({target:{value}}));
const click=(root,label)=>act(async()=>{const node=button(root,label);assert.ok(node,label);assert.equal(!!node.props.disabled,false,label+' enabled');await node.props.onClick()});
const toggle=(root,label,open=true)=>act(async()=>{const node=details(root,label);const target={open};await node.props.onToggle?.({target,currentTarget:target})});
const session={id:'own',title:'Plan the app',workspace:'/workspace',workingDirectory:'/workspace',worktrees:[{id:'copy-a',path:'/copies/app',status:'ready'},{id:'copy-b',path:'/copies/lib',status:'ready'}]};
const intent={status:'available',revision:1,pathCount:0,paths:[],needsReview:false};
const context={workspace:'/workspace',requests:[{requestId:'question-identity',senderSessionId:'own',target:{sessionId:'helper'},delivery:'applied',response:{status:'sealed',outcome:'question',text:'Which version?',qualified:false}}],writeIntent:{sessionId:'own',...intent},awareness:{own:{sessionId:'own',executionDirectory:'/workspace',executionRevision:0,intent},peers:[{sessionId:'peer',title:'Independent notes',status:'working',kind:'root',executionDirectory:'/workspace',sameExecutionDirectory:true,declaredOverlap:true,intent:{...intent,pathCount:1,paths:[{path:'/workspace/notes.md',kind:'file'}]}},{sessionId:'helper',title:'Library helper',kind:'root',commissionedBy:'own',status:'working',executionDirectory:'/copies/lib',sameExecutionDirectory:false,intent}],totalActivePeers:2}};
const list={items:[{target:{sessionId:'own'},title:'Plan the app',kind:'conversation',status:'working'},{target:{sessionId:'helper'},title:'Library helper',kind:'conversation',status:'error',attention:true,collaboration:{creatorSessionId:'own'}}],nextOffset:20};
const accepted=result=>({accepted:true,result});
const candidate=(id='candidate-a',paths=['/copies/app','/copies/lib'])=>({id,sessionId:'own',kind:'capture',status:'matched',purpose:'Combined check',repositories:paths.map(repositoryPath=>({repositoryPath,sourceRevision:'version:'+repositoryPath}))});

test('header exposes workspace work directly without selecting a chat or changing its draft',async()=>{
 const calls=[],opened=[],state={view:{},selectedWorkspaceId:'w',workspaces:[{id:'w',name:'Project',path:'/workspace'}],sessions:[session]};let root;
 await act(async()=>{root=create(React.createElement(WorkNavigationContext.Provider,{value:{}},React.createElement(WorkHeader,{state,session,act:(...args)=>calls.push(args),open:panel=>opened.push(panel),presentation:{layout:{}},narrow:false})))});
 const entry=root.root.findByProps({'aria-label':'Workspace work'});await act(async()=>entry.props.onClick());
 assert.deepEqual(opened,['coordination']);assert.deepEqual(calls,[]);assert.match(content(entry),/Workspace work/);await act(async()=>root.unmount());
});

test('overview reads only bounded workspace projections and leaves messaging controls demand-loaded',async()=>{
 const calls=[];const dispatch=async(action,args)=>{calls.push([action,args]);if(action==='coordination.context')return accepted(context);if(action==='coordination.list')return accepted(list);throw Error('Unexpected action '+action)};let root;
 await act(async()=>{root=create(React.createElement(CoordinationPanel,{dispatch,sessionId:'own',session}))});
 assert.deepEqual(calls,[['coordination.context',{sessionId:'own'}],['coordination.list',{workspace:'/workspace',limit:20,includeWorkers:false}]]);
 const text=content(root.toJSON());for(const fragment of ['Working here','Independent notes','Shares your execution folder','Declared files overlap','Uses a different execution folder','No files declared; overlap is unknown','Library helper','question'])assert.ok(text.includes(fragment),fragment);
 assert.equal(root.root.findAllByProps({'data-action':'coordination.followup'}).length,0);
 await click(root,'Load more workspace chats');assert.deepEqual(calls.at(-1),['coordination.list',{workspace:'/workspace',offset:20,limit:20,includeWorkers:false}]);
 assert.equal(calls.some(([action])=>/send|read|followup|wait|prepare|view.update/.test(action)),false);
 await act(async()=>root.unmount());
});

test('stale context replies never read another workspace or replace the new visit',async()=>{
 let resolveOld;const calls=[];const dispatch=async(action,args)=>{calls.push([action,args]);if(action==='coordination.context'&&args.sessionId==='old')return new Promise(resolve=>{resolveOld=resolve});if(action==='coordination.context')return accepted({...context,requests:[],workspace:'/new',awareness:undefined,writeIntent:undefined});return accepted({items:[],nextOffset:null})};let root;
 await act(async()=>{root=create(React.createElement(RelatedWork,{dispatch,sessionId:'old'}))});
 await act(async()=>root.update(React.createElement(RelatedWork,{dispatch,sessionId:'new'})));
 await act(async()=>resolveOld(accepted({...context,workspace:'/old'})));
 assert.deepEqual(calls.filter(([action])=>action==='coordination.list'),[['coordination.list',{workspace:'/new',limit:20,includeWorkers:false}]]);await act(async()=>root.unmount());
});

test('missing workspace fails closed instead of falling back to an unscoped list',async()=>{
 const calls=[];let root;await act(async()=>{root=create(React.createElement(RelatedWork,{sessionId:'own',dispatch:async(action,args)=>{calls.push(action);return accepted({...context,workspace:''})}}))});
 assert.deepEqual(calls,['coordination.context']);assert.match(content(root.toJSON()),/workspace is unavailable/);await act(async()=>root.unmount());
});

test('repository picker captures inspected versions and recheck/provenance do not replace selected candidate',async()=>{
 const calls=[];const saved=candidate();const dispatch=async(action,args)=>{calls.push([action,args]);if(action==='worktree.inspect')return accepted({repository:{root:args.repositoryPath,sourceRevision:'inspected-exact',branch:'feature',dirty:true}});if(action==='integration.capture')return accepted({...saved,repositories:args.repositories});if(action==='integration.check')return accepted({id:'check',kind:'check',captureId:saved.id,status:'mismatch'});if(action==='runtime.provenance')return accepted({status:'unknown'});throw Error(action)};let root;
 await act(async()=>{root=create(React.createElement(IntegrationEvidence,{session,act:dispatch}))});
 assert.deepEqual(calls,[]);await select(root,'candidate-repository','/copies/app');assert.deepEqual(calls,[]);
 await click(root,'Inspect and add repository');await click(root,'Save candidate observation');
 assert.deepEqual(calls[1][1].repositories,[{repositoryPath:'/copies/app',sourceRevision:'inspected-exact'}]);
 await click(root,'Recheck this candidate');await click(root,'Inspect loaded sources');
 assert.equal(root.root.findByType(IntegrationValidation).props.candidate.id,saved.id);
 assert.equal(root.root.findByProps({id:'validation-repository'}).props.value,'/copies/app');
 assert.equal(calls.filter(([action])=>action==='worktree.inspect').length,1);
 assert.equal(storage.get('amplifier-integration-candidate:own'),saved.id);await act(async()=>root.unmount());
});

test('duplicate repository names preserve exact choices and disclose their distinguishing paths',async()=>{
 const duplicate={...session,worktrees:[{path:'/copies/one/app',status:'ready'},{path:'/copies/two/app',status:'ready'},{path:'/gone/app',status:'archived'}]};let root;
 assert.equal(repositoryChoices(duplicate).some(row=>row.path==='/gone/app'),false);
 await act(async()=>{root=create(React.createElement(IntegrationEvidence,{session:duplicate,act:async()=>{throw Error('No read expected')}}))});
 const options=root.root.findByProps({id:'candidate-repository'}).findAllByType('option');assert.ok(options.some(row=>content(row).includes('/copies/one/app')));assert.ok(options.some(row=>content(row).includes('/copies/two/app')));await act(async()=>root.unmount());
});

test('validation selection stays bound to its exact candidate and collection never reruns a command',async()=>{
 const calls=[],first=candidate(),second=candidate('candidate-b',['/other/app']);const dispatch=async(action,args,meta)=>{calls.push([action,args,meta]);return accepted(action==='integration.validate'?{id:'run-a',kind:'validation',captureId:args.captureId,repositoryPath:args.repositoryPath,command:args.command,phase:'admitting',status:'pending'}:{id:'collection',kind:'collection',captureId:args.captureId,status:'partial',repositories:[]})};let root;
 await act(async()=>{root=create(React.createElement(IntegrationValidation,{session,act:dispatch,candidate:first}))});
 await select(root,'validation-command','npm test');await click(root,'Run validation command');await click(root,'Collect validation results');
 assert.deepEqual(calls[1][1].validationIds,['run-a']);assert.equal(calls[1][1].captureId,'candidate-a');assert.match(content(root.toJSON()),/Combined result: partial/);
 await act(async()=>root.update(React.createElement(IntegrationValidation,{session,act:dispatch,candidate:second})));
 assert.equal(button(root,'Collect validation results').props.disabled,true);assert.equal(root.root.findByProps({id:'validation-repository'}).props.value,'/other/app');assert.doesNotMatch(content(root.toJSON()),/run-a|Combined result: partial/);
 assert.equal(calls.filter(([action])=>action==='integration.validate').length,1);await act(async()=>root.unmount());
});

test('lost acknowledgement survives remount and retries original identity and arguments even after candidate changes',async()=>{
 const calls=[];let dropped=false;const dispatch=async(action,args,meta)=>{calls.push([action,args,meta]);if(!dropped){dropped=true;throw Error('Lost acknowledgement')}return accepted({id:meta.id,kind:'validation',captureId:args.captureId,repositoryPath:args.repositoryPath,command:args.command,phase:'admitting'})};let root;
 await act(async()=>{root=create(React.createElement(IntegrationValidation,{session,act:dispatch,candidate:candidate()}))});await select(root,'validation-command','npm test');await click(root,'Run validation command');assert.ok(storage.has('amplifier-integration-validation:own'));
 await act(async()=>root.unmount());await act(async()=>{root=create(React.createElement(IntegrationValidation,{session,act:dispatch,candidate:candidate('candidate-b')}))});await click(root,'Check the same integration request');
 assert.deepEqual(calls[0],calls[1]);assert.equal(calls[1][1].captureId,'candidate-a');assert.equal(button(root,'Collect validation results').props.disabled,true);assert.equal(storage.has('amplifier-integration-validation:own'),false);await act(async()=>root.unmount());
});

test('saved runs load one bounded page and read only matching candidate records before explicit selection',async()=>{
 const calls=[];const dispatch=async(action,args)=>{calls.push([action,args]);if(action==='integration.list')return accepted({items:[{id:'run-a',kind:'validation',captureId:'candidate-a'},{id:'foreign',kind:'validation',captureId:'candidate-b'},{id:'capture',kind:'capture'}],nextBefore:42});if(action==='integration.read')return accepted({id:'run-a',kind:'validation',captureId:'candidate-a',repositoryPath:'/copies/app',command:'npm test',phase:'admitted'});return accepted({id:'col',kind:'collection',captureId:'candidate-a',status:'passed',repositories:[]})};let root;
 await act(async()=>{root=create(React.createElement(IntegrationValidation,{session,act:dispatch,candidate:candidate()}))});await click(root,'Load saved runs for this candidate');
 assert.deepEqual(calls,[['integration.list',{sessionId:'own'}],['integration.read',{sessionId:'own',id:'run-a'}]]);assert.equal(button(root,'Collect validation results').props.disabled,true);
 await act(async()=>root.root.findByProps({type:'radio'}).props.onChange());await click(root,'Collect validation results');assert.deepEqual(calls.at(-1)[1].validationIds,['run-a']);assert.equal(calls.some(([action])=>action==='integration.validate'),false);await act(async()=>root.unmount());
});

test('a stale saved-run page does not read records after the selected candidate changes',async()=>{
 let finish;const calls=[];const dispatch=async(action,args)=>{calls.push([action,args]);if(action==='integration.list')return new Promise(resolve=>{finish=resolve});throw Error('Obsolete page must not read records')};let root;
 await act(async()=>{root=create(React.createElement(IntegrationValidation,{session,act:dispatch,candidate:candidate()}))});
 await act(async()=>{void button(root,'Load saved runs for this candidate').props.onClick()});
 await act(async()=>root.update(React.createElement(IntegrationValidation,{session,act:dispatch,candidate:candidate('candidate-b')})));
 await act(async()=>finish(accepted({items:[{id:'old',kind:'validation',captureId:'candidate-a'}],nextBefore:4})));
 assert.deepEqual(calls,[['integration.list',{sessionId:'own'}]]);assert.equal(button(root,'Load older saved runs'),undefined);await act(async()=>root.unmount());
});

test('saved candidate restoration is a scoped passive read only when its disclosure is opened',async()=>{
 storage.set('amplifier-integration-candidate:own','retained-candidate');const calls=[];let root;
 const dispatch=async(action,args)=>{calls.push([action,args]);return accepted(candidate('retained-candidate'))};
 await act(async()=>{root=create(React.createElement(IntegrationEvidence,{session,act:dispatch}))});assert.deepEqual(calls,[]);
 await toggle(root,'Combine and check work');assert.deepEqual(calls,[['integration.read',{sessionId:'own',id:'retained-candidate'}]]);
 assert.equal(root.root.findByType(IntegrationValidation).props.candidate.id,'retained-candidate');await act(async()=>root.unmount());
});
