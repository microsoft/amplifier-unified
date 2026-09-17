import {defineConfig} from 'vite';
import {copyFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
const source=fileURLToPath(new URL('./src/converge.css',import.meta.url));
const destination=fileURLToPath(new URL('../amplifier_web/static/converge.amplifier.css',import.meta.url));
export default defineConfig({
 base:'/',
 plugins:[{name:'bundle-shareable-skin',closeBundle(){copyFileSync(source,destination)}}],
 build:{outDir:'../amplifier_web/static',emptyOutDir:true},
 server:{proxy:{'/api':'http://127.0.0.1:8765'}},
});
