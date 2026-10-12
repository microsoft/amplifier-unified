import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,writeFile,readFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {spawnSync,execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {createDistribution} from '../src/index.js';
import {FileStorage} from '@amplifier/ahp-client-kit/file-storage';
const {runTool,inspectCommand}=await import(process.env.HEADLESS_CLIENT_MODULE??'@amplifier/ahp-client-kit/headless');
const python=process.env.AMPLIFIER_ACP_PYTHON;
const setup=String.raw`
import importlib.util,json,sys
from pathlib import Path
p=Path(sys.argv[1]);home=p/'home';home.mkdir();provider=p/'provider';provider.mkdir()
(provider/'pyproject.toml').write_text('[project]\nname="amplifier-module-provider-tool-cli-fixture"\nversion="0.1.0"\n')
module=provider/'amplifier_module_provider_tool_cli_fixture';module.mkdir()
(module/'__init__.py').write_text('''from pathlib import Path
from amplifier_core.models import ProviderInfo,ToolResult,HookResult
class Provider:
    name='fixture'
    def get_info(self):return ProviderInfo(id='fixture',display_name='CLI fixture',defaults={'model':'fixture','max_tokens':4096},capabilities=['tools'])
    async def list_models(self):return [{'id':'fixture'}]
    def parse_tool_calls(self,response):return []
    async def complete(self,*args,**kwargs):raise AssertionError('Direct tool commands must not invoke the model')
async def mount(coordinator,config=None):
    await coordinator.mount('providers',Provider(),name='fixture')
    class Echo:
        name='fixture_echo';description='Owned test effect';input_schema={'type':'object','properties':{'text':{'type':'string'},'deny':{'type':'boolean'}},'required':['text'],'additionalProperties':False}
        async def execute(self,args):
            with Path(__file__).with_name('effects.jsonl').open('a') as f:f.write(args['text']+'\\n')
            return ToolResult(success=True,output=args['text'])
    await coordinator.mount('tools',Echo(),name='fixture_echo')
    async def gate(event,data):return HookResult(action='deny',reason='Denied by owned CLI policy') if data.get('tool_input',{}).get('deny') else HookResult()
    coordinator.hooks.register('tool:pre',gate,name='cli-policy')
''')
context=Path(importlib.util.find_spec('amplifier_module_context_simple').origin).parent
bundle=p/'fixture.yaml';bundle.write_text('bundle:\n  name: headless-tool-fixture\n  version: 1.0.0\nsession:\n  orchestrator:\n    module: loop-live\n  context:\n    module: context-simple\n    source: '+str(context)+'\nproviders:\n  - module: provider-tool-cli-fixture\n    source: '+str(provider)+'\n')
(home/'settings.yaml').write_text('bundle:\n  app: []\n')
(p/'native.json').write_text(json.dumps({'home':str(home),'appHome':str(p/'app'),'bundle':str(bundle),'startupTimeout':60,'adminWorkspaceRoots':[str(p/'workspace')]}))
`;
test('headless tool uses installed Native schemas and hooks, retains result and never prompts or repeats',{skip:!python,timeout:90000},async()=>{
 const directory=await mkdtemp(join(tmpdir(),'unified-cli-tool-native-')),workspace=join(directory,'workspace'),web=join(directory,'web');let app;
 try{
  await mkdir(workspace);await mkdir(web);await writeFile(join(web,'index.html'),'<html><body>Owned fixture</body></html>');
  const prepared=spawnSync(python,['-I','-B','-c',setup,directory],{encoding:'utf8'});assert.equal(prepared.status,0,prepared.stderr);
  app=await createDistribution({account:'owned-cli-tool',stateDirectory:join(directory,'state'),webDirectory:web,defaultWorkspace:workspace,allowedWorkspaceRoots:[workspace],engines:[{id:'amplifier',label:'Native fixture',command:python,args:['-I','-B','-m','amplifier_acp','--config',join(directory,'native.json')]}],nativeAdmin:{engine:'amplifier'}},{authorize:async r=>r.headers.authorization==='Bearer owned-cli-token'?{account:'owned-cli-tool'}:undefined});
  const options={url:app.url.replace(/^http/,'ws')+'/ahp',token:'owned-cli-token',storage:new FileStorage(join(directory,'client')),commandId:'one-tool',workspace,tool:'fixture_echo',arguments:{text:'one effect'},timeoutMs:30000};
  const result=await runTool(options);assert.equal(result.status,'completed',JSON.stringify(result));assert.equal(result.result.result.output,'one effect');
  assert.deepEqual(await inspectCommand(options),result);assert.deepEqual(await runTool(options),result);
  const next={...options,workspace:undefined,session:result.session,commandId:'denied-tool',arguments:{text:'denied',deny:true}};
  const denied=await runTool(next);assert.equal(denied.status,'error');assert.match(denied.result.error.message,/Denied by owned CLI policy/);
  const bad=await runTool({...next,commandId:'schema-tool',arguments:{unknown:true}});assert.notEqual(bad.status,'completed');
  assert.equal(await readFile(join(directory,'provider/amplifier_module_provider_tool_cli_fixture/effects.jsonl'),'utf8'),'one effect\n');
  const token=join(directory,'token');await writeFile(token,'owned-cli-token',{mode:0o600});
  const execute=promisify(execFile),entry=new URL('../src/cli.js',import.meta.url).pathname;
  const common=['--server',options.url,'--token-file',token,'--state-dir',join(directory,'cli-state'),'--command','unified-tool','--output','json'];
  const invoked=JSON.parse((await execute(process.execPath,[entry,'tool','fixture_echo','--args','{"text":"second CLI effect"}','--session',result.session,...common])).stdout);
  assert.equal(invoked.status,'completed');assert.equal(invoked.result.result.output,'second CLI effect');
  assert.deepEqual(JSON.parse((await execute(process.execPath,[entry,'inspect',...common])).stdout),invoked);
  assert.equal(await readFile(join(directory,'provider/amplifier_module_provider_tool_cli_fixture/effects.jsonl'),'utf8'),'one effect\nsecond CLI effect\n');
  const history=await app.host.readSessionContext(result.session,10);assert.equal(history.messages.filter(m=>m.role==='user').length,0);
 }finally{await app?.close();await rm(directory,{recursive:true,force:true})}
});
