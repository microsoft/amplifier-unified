import {defineConfig} from 'vite';
import {copyFileSync,mkdirSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
const source=fileURLToPath(new URL('./src/converge.css',import.meta.url));
const destination=fileURLToPath(new URL('../amplifier_web/static/converge.amplifier.css',import.meta.url));
export default defineConfig({
 base:'/',
 plugins:[{name:'bundle-shareable-skin',closeBundle(){
  copyFileSync(source,destination);
  const vendor=fileURLToPath(new URL('../amplifier_web/static/vendor',import.meta.url));mkdirSync(vendor,{recursive:true});
  copyFileSync(fileURLToPath(new URL('./node_modules/babylonjs/babylon.js',import.meta.url)),`${vendor}/babylon.js`);
  const licenses=fileURLToPath(new URL('../amplifier_web/static/licenses',import.meta.url));mkdirSync(licenses,{recursive:true});
  copyFileSync(fileURLToPath(new URL('./node_modules/babylonjs/license.md',import.meta.url)),`${licenses}/babylonjs.txt`);
  copyFileSync(fileURLToPath(new URL('./node_modules/babylonjs/NOTICE.md',import.meta.url)),`${licenses}/babylonjs-notice.txt`);
  for(const [name,file] of [['@modelcontextprotocol/ext-apps','mcp-apps'],['@modelcontextprotocol/client','mcp-client'],['@modelcontextprotocol/core','mcp-core'],['zod','zod']])copyFileSync(fileURLToPath(new URL(`./node_modules/${name}/LICENSE`,import.meta.url)),`${licenses}/${file}.txt`);
  for(const name of ['mermaid','dompurify','highlight.js'])copyFileSync(fileURLToPath(new URL(`./node_modules/${name}/LICENSE`,import.meta.url)),`${licenses}/${name}.txt`);
 }}],
 build:{outDir:'../amplifier_web/static',emptyOutDir:true},
 server:{proxy:{'/api':'http://127.0.0.1:8765'}},
});
