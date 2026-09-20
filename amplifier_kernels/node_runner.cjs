// Executed only in the owned Node child. This VM is a namespace, not a sandbox.
const readline = require('node:readline');
const vm = require('node:vm');
const util = require('node:util');
const {createRequire} = require('node:module');
const path = require('node:path');
const {AsyncLocalStorage} = require('node:async_hooks');
const scope = new AsyncLocalStorage();
const wire = process.stdout.write.bind(process.stdout);
let cell = null;
function send(value) { wire(JSON.stringify(value) + '\n'); }
for (const [stream, target] of [['stdout', process.stdout], ['stderr', process.stderr]]) {
  target.write = (value, encoding, callback) => {
    const text = Buffer.isBuffer(value) ? value.toString(typeof encoding === 'string' ? encoding : 'utf8') : String(value);
    const characters = Array.from(text);
    for (let offset = 0; offset < characters.length; offset += 1000)
      send({type:'output', cellId:scope.getStore()??null, stream, text:characters.slice(offset,offset+1000).join(''), encoding_loss:Buffer.isBuffer(value)&&!Buffer.from(text,'utf8').equals(value)});
    if (typeof encoding === 'function') encoding();
    if (typeof callback === 'function') callback();
    return true;
  };
}
globalThis.require = createRequire(path.join(process.cwd(), '__kernel__.cjs'));
send({type:'ready',runtime:{language:'node',executable:process.execPath,version:process.version,
  pid:process.pid,versions:{node:process.versions.node,v8:process.versions.v8},packages:{}}});
(async () => {
 for await (const line of readline.createInterface({input:process.stdin,crlfDelay:Infinity})) {
  const request=JSON.parse(line); cell=request.cellId;
  try {
   const result=await scope.run(cell,()=>vm.runInThisContext(request.code,{filename:'<cell>'}));
   const rendered=result===undefined?null:util.inspect(result,{depth:4,maxArrayLength:100,maxStringLength:4000,customInspect:false});
   send({type:'done',cellId:cell,success:true,result:rendered?.slice(0,4000)??null,resultTruncated:!!rendered&&rendered.length>4000});
  } catch(error) {
   send({type:'done',cellId:cell,success:false,error:String(error?.stack??error).slice(-4000)});
  } finally { cell=null; }
 }
})().catch(error=>{send({type:'fatal',error:String(error).slice(0,2000)});process.exitCode=1;});
