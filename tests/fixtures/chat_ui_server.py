"""Isolated chat shell fixture. Never contacts a model provider."""
import asyncio, copy, json, os, signal, sys
from pathlib import Path
import tempfile
from aiohttp import web
import settings_ui_server as fixture
class ChatRuntime(fixture.Runtime):
 def __init__(self):
  super().__init__();self.selection=None;self.sent=[];self.edits=[];self.emitters={}
 async def start(self,session,emit,*,preserve_emit=False):
  self.emitters[session['id']]=emit
  await super().start(session,emit,preserve_emit=preserve_emit)
 async def control(self,sid,operation,args):
  if operation=='history.edit':
   # Synthetic worker boundary, preserving the real preparation and service
   # revision path. Actual Core rewind/checkpoint semantics are qualified by
   # history_edit_probe.py; this fixture proves browser admission and effects.
   from amplifier_web.session_store import fork_session
   source=args['source'];prepared=fork_session(self.home,source,sid,
    before_message_id=args['messageId'],prepare_only=True,
    expected_context_digest=source.get('historyEditContextDigest'))
   result={'messages':prepared['messages'],'throughTurn':prepared['throughTurn'],
    'operationId':args['operationId'],'eventsPreserved':True,
    'sharedHistoryOffset':source.get('sharedHistoryOffset',0),
    'sharedHistoryUserTurnOffset':source.get('sharedHistoryUserTurnOffset',0),
    'sharedHistoryTotal':source.get('sharedHistoryOffset',0)+len(prepared['messages'])+1}
   self.edits.append(args['operationId']);emit=self.emitters[sid]
   await emit('history.revised',{**result,'sessionId':sid})
   await self.send(source,args['text'],args['operationId'],emit)
   return result
  if operation=='provider.select':self.selection=args.copy();return {'selection':self.selection}
  if operation=='provider.reset':self.selection=None;return {'selection':None}
  if operation=='configuration.providers':
   return {'providers':[{'id':'openai','info':{'display_name':'Fixture OpenAI','defaults':{'model':'fixture-model'},'config_fields':[{'id':'reasoning_effort','choices':['low','medium','high'],'field_type':'choice'}]},'supports':{'models':True}}], 'effective':{'instance':'openai','model':(self.selection or {}).get('model','fixture-model'),'effort':(self.selection or {}).get('effort')},'pinned':bool(self.selection),'selection':self.selection}
  if operation=='configuration.providerModels':return {'provider':'openai','models':[{'id':'fixture-model'},{'id':'fixture-vision'}]}
  return await super().control(sid,operation,args)
 async def send(self,session,text,input_id,emit):
  self.sent.append({'sessionId':session['id'],'text':text,'inputId':input_id})
  await emit('runtime.status',{'sessionId':session['id'],'status':'working'})
  await asyncio.sleep(.3)
  await emit('assistant.message',{'sessionId':session['id'],'text':'## Ready\n\nI received your message and attachments.\n\n'+('\n\nA paragraph with **Markdown** and a useful result.'*30),'inputId':input_id})
  await emit('runtime.generation',{'sessionId':session['id'],'event':'generation.finished','generation_id':'fixture','input_ids':[input_id],'text':'Ready','active_job_ids':[],'disposition':'manager_turn_finished'})
  await emit('runtime.status',{'sessionId':session['id'],'status':'idle'})
fixture.Runtime=ChatRuntime


from amplifier_web import draft_defaults
_original_defaults=draft_defaults.resolve_defaults
async def fixture_defaults(*args,**kwargs):
 if not kwargs.get('catalog'):return await _original_defaults(*args,**kwargs)
 return {'providers':[{'id':'openai','sharedCatalogKey':'fixture-openai','info':{'display_name':'Fixture OpenAI','defaults':{'model':'fixture-model','reasoning_effort':'medium'}}}],
         'effective':{'instance':'openai','model':'fixture-model','effort':'medium'},
         'catalogs':{'fixture-openai':{'phase':'ready','models':[{'id':'fixture-model'},{'id':'fixture-vision'}],
             'metadata':{'info':{'display_name':'Fixture OpenAI'},'configSchema':{'fields':[{'id':'reasoning_effort','choices':['low','medium','high'],'field_type':'choice'}]}}}}}
draft_defaults.resolve_defaults=fixture_defaults

async def main(home):
 app=await fixture.main(home)
 app['service'].runtime.home=home
 async def inspect(request):
  if request.headers.get('Authorization')!='Bearer fixture-browser-control-token':
   raise web.HTTPForbidden()
  service=app['service']
  async with service.lock:
   return web.json_response({'pins':copy.deepcopy(service._state['pinnedSessionIds']),
    'sessions':[{key:copy.deepcopy(row.get(key)) for key in ('id','title','workspaceId','messages')} for row in service._state['sessions']],
    'sends':copy.deepcopy(service.runtime.sent),'edits':copy.deepcopy(service.runtime.edits)})
 app.router.add_get('/fixture/message-source',inspect)
 return app

async def serve_dynamic(home):
 app=await main(home);runner=web.AppRunner(app);await runner.setup()
 stopped=asyncio.Event();loop=asyncio.get_running_loop()
 signals=(signal.SIGTERM,signal.SIGINT) if os.name=='posix' else ()
 for signum in signals:loop.add_signal_handler(signum,stopped.set)
 try:
  site=web.TCPSite(runner,'127.0.0.1',0);await site.start()
  base=f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
  app['allowed_origins']=app['allowed_origins']|{base}
  print(json.dumps({'fixture':'chat-ui','pid':os.getpid(),'base':base}),flush=True)
  await stopped.wait()
 finally:
  try:await runner.cleanup()
  finally:
   for signum in signals:loop.remove_signal_handler(signum)

if __name__=='__main__':
 with tempfile.TemporaryDirectory(prefix='amplifier-chat-ui-') as tmp:
  if '--dynamic-port' in sys.argv:
   asyncio.run(serve_dynamic(Path(tmp)));sys.exit(0)
  # aiohttp calls this only after the fixture itself successfully binds.
  ready=(lambda _: print('PWA fixture bound http://127.0.0.1:8958',flush=True)) if '--print-ready' in sys.argv else None
  web.run_app(main(Path(tmp)),host='127.0.0.1',port=8958,print=ready)
