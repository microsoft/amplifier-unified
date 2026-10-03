import {mkdir,writeFile,rename,realpath} from 'node:fs/promises';
import {join,relative,isAbsolute} from 'node:path';
import {randomUUID} from 'node:crypto';
import {createPortabilityCapabilities,TransferConnection} from '@amplifier/unified-portability-capability';

/** Signed pairing and evidence validation belong to the private portability owner. */
export async function composePortability(config,context,{engines,roots,host,evidenceOwners,omissions,authorizeTransfer}){
 const dataDir=join(context.directory,'portability'),path=join(context.directory,'portability-launch.json');
 if(!config.stageDir||!config.exchangeDir)throw Error('Portability requires explicit owned stageDir and exchangeDir');
 await mkdir(dataDir,{recursive:true,mode:0o700});await mkdir(config.stageDir,{recursive:true,mode:0o700});
 const stageDir=await realpath(config.stageDir);
 if(!roots.some(root=>{const rel=relative(root,stageDir);return !rel||rel!=='..'&&!rel.startsWith('../')&&!isAbsolute(rel);}))throw Error('Transfer staging must be within configured workspace roots');
 const selected=config.engines;if(!Array.isArray(selected)||!selected.length||new Set(selected).size!==selected.length)throw Error('Portability requires explicitly allowed engine IDs');
 const connections=new Map();for(const id of selected){const engine=engines.find(value=>value.id===id);if(!engine)throw Error('Unknown portability engine');connections.set(id,new TransferConnection({...engine,cwd:stageDir}));}
 const temporary=path+'.'+randomUUID();await writeFile(temporary,JSON.stringify({dataDir,label:config.label??'Amplifier Unified',workspaceRoots:roots,stageDir,exchangeDir:config.exchangeDir}),{mode:0o600});await rename(temporary,path);
 if(!config.owner&&!config.python&&!config.command)throw Error('Portability requires an independently installed owner');
 const launcher=config.owner??{command:config.command??config.python,args:config.command?['--config',path]:['-I','-m','amplifier_unified_portability.server','--config',path],env:config.env,cwd:dataDir};
 const get=name=>{const owner=evidenceOwners.get(name);if(!owner)throw Error('Portable evidence owner is not installed: '+name);return owner;};
 const child=(args,step)=>({...args,commandId:'portability:'+args.commandId+':'+step});
 const cap=createPortabilityCapabilities({...context,owner:launcher,authorizeTransfer,
  beginTransfer:(session,args)=>host().beginTransfer(session,child(args,'begin')),commitTransfer:(session,args)=>host().commitTransfer(session,child(args,'commit')),cancelTransfer:(session,args)=>host().cancelTransfer(session,child(args,'cancel')),adoptTransferredSession:args=>host().adoptTransferredSession(child(args,'adopt')),
  nativeTransfer:args=>{const peer=connections.get(args.engineId);if(!peer)throw Error('Transfer engine is not authorized on this host');return peer.perform(args);},
  exportTransferEvidence:async args=>{const evidence=[];let remaining=Math.min(args.limitBytes??8*1024*1024,8*1024*1024);for(const [name,owner]of evidenceOwners){if(remaining<1024)throw Error('Aggregate transfer evidence exhausted; narrow source evidence explicitly');const value=await owner.exportTransferEvidence({...args,limitBytes:remaining});if(value.owner!==name||!Number.isSafeInteger(value.bytes)||value.bytes<0||value.bytes>remaining)throw Error('Owner exceeded transfer evidence budget');evidence.push(value);remaining-=value.bytes;}return {evidence,omissions};},
  stageTransferEvidence:async args=>{const receipts=[];for(const evidence of args.evidence)receipts.push(await get(evidence.owner).stageTransferEvidence({...args,evidence}));return {receipts,omissions:[]};},
  activateTransferEvidence:async args=>{const receipts=[];for(const evidence of args.evidence)receipts.push(await get(evidence.owner).activateTransferEvidence({...args,evidenceHash:evidence.sha256}));return {receipts,omissions:[]};},
 });
 const close=cap.close;cap.close=async()=>{await close();await Promise.allSettled([...connections.values()].map(peer=>peer.close()));};
 try{const snapshot=await cap.read({uri:cap.manifest.topics.portability.uri,topic:'portability',scope:'host',clientId:''});const identity=snapshot.data.portability.host.id;if(!/^[a-f0-9]{64}$/.test(identity))throw Error('Invalid trusted transfer identity');return {owner:cap,identity};}catch(error){await cap.close();throw error;}
}
