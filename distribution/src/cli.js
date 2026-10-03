#!/usr/bin/env node
import {readFile} from 'node:fs/promises';
import {createDistribution} from './index.js';
const index=process.argv.indexOf('--config');if(index<0||!process.argv[index+1])throw Error('Usage: amplifier-unified --config /absolute/distribution.json');
const config=JSON.parse(await readFile(process.argv[index+1],'utf8'));
const app=await createDistribution(config);
process.stdout.write(JSON.stringify({url:app.url,protocol:'0.9.0',pid:process.pid})+'\n');
let stopping=false;async function stop(){if(stopping)return;stopping=true;await app.close();process.exit(0);}
process.once('SIGINT',()=>void stop());process.once('SIGTERM',()=>void stop());
