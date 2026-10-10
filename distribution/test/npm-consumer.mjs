// Qualification tooling only; never an installed runtime or package installer.
import {writeFile,readFile,realpath,lstat} from 'node:fs/promises';
import {join,relative,resolve,sep} from 'node:path';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {pathToFileURL} from 'node:url';
const execute=promisify(execFile);
const flags=directory=>['--prefix',directory,'--global=false','--workspaces=false'];
const within=(root,path)=>{const part=relative(root,path);return part===''||(!part.startsWith('..'+sep)&&part!=='..'&&!part.startsWith(sep));};

/** Claim a fresh caller-created directory beneath its explicit owned allocation.
 * Exclusive manifest creation refuses existing/parent projects before npm runs.
 */
export async function prepareNpmConsumer(directory,ownedRoot,{name='owned-qualification-consumer'}={}){
 const root=await realpath(ownedRoot),target=await realpath(directory);
 if(!within(root,target))throw Error('npm consumer is outside its owned allocation');
 if((await lstat(directory)).isSymbolicLink())throw Error('npm consumer cannot be a symlink');
 const manifest=JSON.stringify({name,private:true,type:'module'});
 await writeFile(join(target,'package.json'),manifest,{flag:'wx',mode:0o600});
 return {directory:target,ownedRoot:root,manifest};
}

/** Verify npm's actual resolved destinations before the first install write. */
export async function installNpmConsumer(consumer,packages,{env=process.env,omitDev=false}={}){
 const {directory,ownedRoot,manifest}=consumer;
 if(await realpath(directory)!==directory||!within(ownedRoot,directory))throw Error('npm consumer root changed');
 for(const name of ['package.json','node_modules']){
  try{if((await lstat(join(directory,name))).isSymbolicLink())throw Error('npm consumer destination cannot be a symlink');}
  catch(error){if(error.code!=='ENOENT')throw error;}
 }
 if(await readFile(join(directory,'package.json'),'utf8')!==manifest)throw Error('npm consumer manifest changed');
 const options={cwd:directory,env:{...env,NODE_PATH:''},timeout:90000,maxBuffer:1024*1024};
 for(const [command,expected]of [['prefix',directory],['root',join(directory,'node_modules')]]){
  const actual=(await execute('npm',[command,...flags(directory)],options)).stdout.trim();
  if(resolve(actual)!==expected)throw Error('npm resolved outside the owned consumer');
 }
 return execute('npm',['install',...flags(directory),'--ignore-scripts','--no-audit','--no-fund',...(omitDev?['--omit=dev']:[]),...packages],options);
}

if(process.argv[1]&&import.meta.url===pathToFileURL(resolve(process.argv[1])).href){
 const [directory,ownedRoot,...packages]=process.argv.slice(2);
 if(!directory||!ownedRoot||!packages.length)throw Error('Usage: npm-consumer.mjs directory owned-root archive...');
 await installNpmConsumer(await prepareNpmConsumer(directory,ownedRoot),packages);
}
