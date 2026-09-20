// Host-owned validation; uploaded modules cannot submit validation receipts.
import {readFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {fileURLToPath,pathToFileURL} from 'node:url';
import path from 'node:path';
const [toolchain,modulePath,manifestJSON]=process.argv.slice(2);
const manifest=JSON.parse(manifestJSON);
const {parseAst}=await import(pathToFileURL(path.join(toolchain,'rollup/dist/es/shared/parseAst.js')));
const {chromium}=await import(pathToFileURL(path.join(toolchain,'playwright/index.mjs')));
const source=await readFile(modulePath,'utf8');
const tree=parseAst(source);
function inspect(node){
 if(!node||typeof node!=='object')return;
 if(['ImportDeclaration','ImportExpression','ExportAllDeclaration'].includes(node.type)||node.type==='ExportNamedDeclaration'&&node.source)throw Error('Bundle all dependencies into one ESM artifact; imports are not supported in this profile.');
 // The native profile is trusted code. These checks enforce packaging, not
 // isolation: same-origin native code retains the authority of the web app.
 if(node.type==='Identifier'&&['eval','Function'].includes(node.name))throw Error('Dynamic code generation is not supported.');
 for(const value of Object.values(node))if(Array.isArray(value))value.forEach(inspect);else if(value&&typeof value==='object')inspect(value);
}
inspect(tree);
if(/react\.production|react\.development|react\.transitional\.element/.test(source))throw Error('Use host React; do not bundle a second React runtime.');
const directory=fileURLToPath(new URL('./static/',import.meta.url));
const server=createServer(async(req,res)=>{
 try{
  const url=new URL(req.url,'http://localhost');
  const file=url.pathname==='/module.mjs'?modulePath:path.join(directory,url.pathname==='/'?'shell-validation.html':url.pathname);
  if(file!==modulePath&&!file.startsWith(directory))throw Error('Invalid path');
  if(file!==modulePath&&!file.endsWith('.js')&&!file.endsWith('shell-validation.html'))throw Error('Unsupported fixture resource');
  res.setHeader('Content-Type',file.endsWith('.html')?'text/html':'text/javascript');
  res.end(await readFile(file));
 }catch{res.writeHead(404).end()}
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
let browser;
try{
 browser=await chromium.launch({headless:true});
 const page=await browser.newPage();
 const origin=`http://127.0.0.1:${server.address().port}`,errors=[];
 await page.route('**/*',route=>route.request().url().startsWith(origin+'/')?route.continue():route.abort());
 page.on('pageerror',error=>errors.push(error.message));
 await page.goto(origin);
 await page.waitForFunction(()=>typeof window.validateShellModule==='function');
 const result=await page.evaluate(()=>window.validateShellModule());
 if(errors.length)throw Error(errors.join('\n'));
 process.stdout.write(JSON.stringify({...result,profile:manifest.profile}));
}finally{
 await browser?.close();
 await new Promise(resolve=>server.close(resolve));
}
