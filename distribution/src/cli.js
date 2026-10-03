#!/usr/bin/env node
import {readFile} from 'node:fs/promises';
import {startConfiguredDistribution} from './launch.js';
const index=process.argv.indexOf('--config');if(index<0||!process.argv[index+1])throw Error('Usage: amplifier-unified --config /absolute/distribution.json');
const config=JSON.parse(await readFile(process.argv[index+1],'utf8'));
let app,stopping=false;
async function stop(){
 if(stopping)return;
 if(!app){process.stderr.write(JSON.stringify({status:'refused',code:'service_starting'})+'\n');return;}
 stopping=true;
 try{await app.requestStop();process.exit(0);}
 catch{stopping=false;process.stderr.write(JSON.stringify({status:'refused',code:'owned_service_stop_requires_held_admission'})+'\n');}
}
// Install before initialization in owned service mode; repeated signals are not
// permission to interrupt active work or abandon a partially initialized owner.
const ownedService=!!config.supervision?.serviceLifecycle;
if(ownedService){process.on('SIGINT',()=>void stop());process.on('SIGTERM',()=>void stop());}
app=await startConfiguredDistribution(config,{entrypointUrl:import.meta.url});
process.stdout.write(JSON.stringify({url:app.url,protocol:'0.9.0',pid:process.pid})+'\n');
if(!ownedService){process.once('SIGINT',()=>void stop());process.once('SIGTERM',()=>void stop());}
