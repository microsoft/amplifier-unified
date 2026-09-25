"""Isolated settings UI fixture: synthetic providers and runtime, no account calls."""
import asyncio,copy,os,sys,tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from aiohttp import web
from amplifier_web.server import create_app
from amplifier_web.host.config import write_private
from amplifier_web.setup import SetupManager
from amplifier_web.bundles import BundleManager
from amplifier_web.updates import group_sources
from amplifier_web.smart_tools import SmartToolsManager
import yaml
PLAN={'session':{'orchestrator':{'module':'loop-live','config':{'max_iterations':10}},'context':{'module':'context-simple','config':{'max_tokens':1000}}},'providers':[{'module':'provider-openai','id':'openai','config':{'default_model':'fixture-model'}}],'tools':[{'module':'tool-filesystem','config':{'read_only':False,'max_bytes':2000}}],'hooks':[{'module':'hooks-logging','config':{'enabled':True}}]}
class Runtime:
 def __init__(self):self.plan=copy.deepcopy(PLAN)
 async def start(self,session,emit):await emit('runtime.status',{'sessionId':session['id'],'status':'ready'})
 async def stop(self,*args):pass
 async def close(self):pass
 async def control(self,sid,operation,args):
  if operation=='configuration.apply':self.plan=copy.deepcopy(args['config']);return {'requiresRestart':True}
  if operation=='configuration.inspect':return {'plan':self.plan,'capabilities':{'configuration':True}}
  return {}
async def probe(self,action,args,workspace):
 await asyncio.sleep(.25)
 result={'providerMetadata':{'module':args.get('module','provider-openai'),'info':{'config_fields':[{'id':'reasoning_effort','display_name':'Reasoning effort','choices':['low','high'],'field_type':'choice'}]},'configSchema':{}}}
 if action=='providers.models':result.update(models=[{'id':'fixture-model'},{'id':'fixture-alternative'}],modelsProviderId=args['id'])
 if action=='providers.test':result['test']={'reachable':True,'modelCount':1,'providerId':args['id'],'method':'provider.list_models'}
 return result
SetupManager.probe=probe
async def cached_routing(self,workspace):pass
SetupManager.ensure_routing_catalog=cached_routing
async def discover(self,url):
 return {'candidates':[{'name':name,'path':name+'.yaml','uri':url+'#'+name+'.yaml','kind':'behavior'} for name in ['base','dev-tools','research']]}
BundleManager.discover=discover
# Exercise real operation receipts and connection persistence without fetching
# or executing third-party packages during settings acceptance.
async def catalog(self):
 await asyncio.sleep(.2)
 rows=[{'id':'fixture-tool','name':'Fixture catalog tool','description':'A discoverable specialist tool','repository':'https://example.com/fixture-tool','ref':'main','path':'.'}]
 await self._change(lambda state:state.update(catalog=rows))
 return {'items':rows,'commit':'fixture-revision'}
async def inspect_tool(self,args):
 await asyncio.sleep(.2)
 return {**args,'name':'Fixture catalog tool','description':'A discoverable specialist tool','commit':'fixture-revision','manifest':{'name':'Fixture catalog tool','version':'1.0','requires':'Python 3.13'},'pythonSupported':True}
async def install_tool(self,args):
 await asyncio.sleep(.2)
 result={**args,'id':'fixture-tool','name':'Fixture catalog tool','commit':'fixture-revision','binDir':'/fixture/bin','status':'installed','guidance':'Configure the documented MCP executable.'}
 await self._change(lambda state:state.update(installations=[result]))
 return result
SmartToolsManager.catalog=catalog
SmartToolsManager.inspect=inspect_tool
SmartToolsManager.install=install_tool
async def main(home):
 os.environ['AMPLIFIER_HOME']=str(home/'native')
 os.environ['AMPLIFIER_WEB_HOME']=str(home);os.environ['AMPLIFIER_UNIFIED_IMPORT_HOME']=str(home/'legacy');os.environ['FIXTURE_KEY']='fixture-private-key'
 workspace=home/'workspace';workspace.mkdir();(workspace/'project').mkdir();(workspace/'project'/'bundle.yaml').write_text('bundle:\n  name: fixture\n')
 write_private(Path(os.environ['AMPLIFIER_HOME'])/'settings.yaml',yaml.safe_dump({'config':{'providers':[{'id':name,'module':'provider-openai','config':{'api_key':'${FIXTURE_KEY}','default_model':'fixture-model'}} for name in ['one','two','three']]},'routing':{'matrix':'balanced'},'bundle':{'added':{'fixture-root':'foundation:test'}}}))
 matrix={'name':'balanced','roles':{role:{'description':role.title(),'candidates':[{'provider':'one','model':'fixture-model'},{'provider':'two','model':'fallback-model'}]} for role in ['general','fast']}}
 write_private(home/'config/routing/balanced.yaml',yaml.safe_dump(matrix))
 app=await create_app(home,workspace=workspace,runtime=Runtime(),voice=False,background_updates=False)
 # The browser fixture uses a synthetic control credential, never local PAM.
 if 'control_token' in app:
  app['control_token']='fixture-browser-control-token'
  app['allowed_origins']=app['allowed_origins']|{'http://127.0.0.1:8957','http://127.0.0.1:8958'}
 service=app['service'];await service.dispatch('session.create',{'title':'Settings test','workspace':str(workspace),'bundle':'anchors'})
 async def agent_view(request):
  payload=await request.json()
  result=await service.app_bridge('dispatch',{'action':'view.update','args':{'clientId':payload['clientId'],'patch':payload['patch']}},service._session()['id'])
  return web.json_response(result)
 app.router.add_post('/fixture/agent-view',agent_view)
 service.state['updates']['items']=group_sources([{'id':str(i),'label':f'fixture-source-{i}','status':'current'} for i in range(86)]+[{'id':'copy-'+str(i),'label':'github.com/example/amplifier-bundle-computer-use','kind':'bundle / module','ref':'main','current':'123456789','latest':'abcdefghi','status':'update'} for i in range(2)])
 service.state['updates']['available']=1
 return app
if __name__=='__main__':
 with tempfile.TemporaryDirectory(prefix='amplifier-settings-ui-') as tmp:
  web.run_app(main(Path(tmp)),host='127.0.0.1',port=8957,print=None)
