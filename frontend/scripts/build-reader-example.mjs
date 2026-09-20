import {build} from 'esbuild';
import {fileURLToPath} from 'node:url';
import {mkdir,copyFile} from 'node:fs/promises';
const root=fileURLToPath(new URL('../../',import.meta.url));
await mkdir(root+'examples/shell-reader/dist',{recursive:true});
await build({entryPoints:[root+'examples/shell-reader/index.js'],outfile:root+'examples/shell-reader/dist/module.mjs',bundle:true,format:'esm',platform:'browser',target:'es2022'});
await copyFile(root+'examples/shell-reader/manifest.json',root+'examples/shell-reader/dist/manifest.json');
