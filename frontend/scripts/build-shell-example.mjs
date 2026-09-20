import {build} from 'esbuild';
import {fileURLToPath} from 'node:url';
import {mkdir,copyFile} from 'node:fs/promises';
const root=fileURLToPath(new URL('../../',import.meta.url));
await mkdir(root+'examples/shell-navigator/dist',{recursive:true});
await build({entryPoints:[root+'examples/shell-navigator/index.js'],outfile:root+'examples/shell-navigator/dist/module.mjs',bundle:true,format:'esm',platform:'browser',target:'es2022'});
await copyFile(root+'examples/shell-navigator/manifest.json',root+'examples/shell-navigator/dist/manifest.json');
