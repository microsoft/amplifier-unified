// Assemble only the two compiled components and their existing runtime deps.
// No dependency installation, full distribution build, or production signing.
import {mkdir,readFile,realpath,writeFile,readdir,stat,cp} from 'node:fs/promises';
import {existsSync} from 'node:fs';
import {createRequire} from 'node:module';
import {join,resolve} from 'node:path';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {createHash} from 'node:crypto';
import * as tar from 'tar';
const exec=promisify(execFile);
const [output,hostInput,ownerInput,hostArchive]=process.argv.slice(2).map(p=>resolve(p));
await mkdir(output,{mode:0o700});
const stage=join(output,'payload'),packs=join(output,'packs');
await mkdir(stage);await mkdir(packs);
const inputs=new Map(),receipt=[];
async function collect(directory){
  const pkg=JSON.parse(await readFile(join(directory,'package.json'),'utf8'));
  if(inputs.has(pkg.name)){
    if(inputs.get(pkg.name).pkg.version!==pkg.version)throw Error('fixture_dependency_version_conflict');
    return;
  }
  inputs.set(pkg.name,{directory,pkg});
  const req=createRequire(join(directory,'package.json'));
  const names=new Set([...Object.keys(pkg.dependencies??{}),...Object.keys(pkg.peerDependencies??{})
    .filter(name=>!pkg.peerDependenciesMeta?.[name]?.optional)]);
  for(const name of names){
    const dependency=req.resolve.paths(name).map(base=>join(base,name)).find(base=>existsSync(join(base,'package.json')));
    if(!dependency)throw Error('existing_fixture_dependency_missing: '+name);
    await collect(await realpath(dependency));
  }
}
await collect(hostInput);await collect(ownerInput);
for(const [name,{directory,pkg}] of inputs){
  let archive;
  if (name==='@amplifier/unified-host' && hostArchive) {
    archive=join(packs,'sealed-host.tgz');await cp(hostArchive,archive);
  } else {
    const packed=JSON.parse((await exec('npm',['pack','--ignore-scripts','--json','--pack-destination',packs],
      {cwd:directory,maxBuffer:1024*1024})).stdout)[0];
    archive=join(packs,packed.filename);
  }
  const destination=join(stage,'node_modules',name);
  await mkdir(destination,{recursive:true});
  // Bundled dependencies are assembled once from their independently recorded
  // installed package above; do not retain a redundant nested package forest.
  await tar.x({file:archive,cwd:destination,strip:1,strict:true,
    filter:(path,entry)=>!path.startsWith('package/node_modules/')&&['File','Directory'].includes(entry.type)});
  receipt.push({name,version:pkg.version,archiveSha256:createHash('sha256').update(await readFile(archive)).digest('hex')});
}
await writeFile(join(stage,'package.json'),JSON.stringify({name:'linux-host-lifecycle-fixture',version:'1.0.0',type:'module',
  dependencies:{'@amplifier/unified-host':inputs.get('@amplifier/unified-host').pkg.version,
    '@amplifier/unified-distribution-update-owner':inputs.get('@amplifier/unified-distribution-update-owner').pkg.version}}));
await cp(new URL('./signed-host-runtime.mjs',import.meta.url),join(stage,'server.mjs'));
await cp(new URL('./installed-supervisor.mjs',import.meta.url),join(stage,'controller.mjs'));
await tar.c({cwd:output,file:join(output,'payload.tgz'),gzip:true,portable:true},['payload']);
await writeFile(join(output,'INPUTS.json'),JSON.stringify({schema:'owned-host-fixture-inputs-v1',packages:receipt},null,2)+'\n');
console.log(JSON.stringify({packages:receipt.length,archiveBytes:(await stat(join(output,'payload.tgz'))).size}));
