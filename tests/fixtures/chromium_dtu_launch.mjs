// Optional owned-DTU launch configuration, including nested validator processes.
// Activate explicitly with NODE_OPTIONS=--import=<absolute path>; never used by
// production startup. Browser execution and all product assertions stay real.
import {chromium} from '../../frontend/node_modules/playwright/index.mjs';
const args=JSON.parse(process.env.AMPLIFIER_CHROMIUM_ARGS||'[]');
if(!Array.isArray(args)||!args.every(value=>typeof value==='string'))throw Error('Invalid Chromium launch arguments');
if(args.length){
 const launch=chromium.launch.bind(chromium);
 chromium.launch=(options={})=>launch({...options,args:[...new Set([...(options.args||[]),...args])]});
}
