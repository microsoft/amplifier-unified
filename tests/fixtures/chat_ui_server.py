"""Isolated chat shell fixture. Never contacts a model provider."""
import asyncio
from pathlib import Path
import tempfile
from aiohttp import web
import settings_ui_server as fixture
class ChatRuntime(fixture.Runtime):
 def __init__(self):
  super().__init__();self.selection=None
 async def control(self,sid,operation,args):
  if operation=='provider.select':self.selection=args.copy();return {'selection':self.selection}
  if operation=='provider.reset':self.selection=None;return {'selection':None}
  if operation=='configuration.providers':
   return {'providers':[{'id':'openai','info':{'display_name':'Fixture OpenAI','defaults':{'model':'fixture-model'},'config_fields':[{'id':'reasoning_effort','choices':['low','medium','high'],'field_type':'choice'}]},'supports':{'models':True}}], 'effective':{'instance':'openai','model':(self.selection or {}).get('model','fixture-model'),'effort':(self.selection or {}).get('effort')},'pinned':bool(self.selection),'selection':self.selection}
  if operation=='configuration.providerModels':return {'provider':'openai','models':[{'id':'fixture-model'},{'id':'fixture-vision'}]}
  return await super().control(sid,operation,args)
 async def send(self,session,text,input_id,emit):
  await emit('runtime.status',{'sessionId':session['id'],'status':'working'})
  await asyncio.sleep(.3)
  await emit('assistant.message',{'sessionId':session['id'],'text':'## Ready\n\nI received your message and attachments.\n\n'+('\n\nA paragraph with **Markdown** and a useful result.'*30),'inputId':input_id})
  await emit('runtime.generation',{'sessionId':session['id'],'event':'generation.finished','generation_id':'fixture','input_ids':[input_id],'text':'Ready','active_job_ids':[],'disposition':'manager_turn_finished'})
  await emit('runtime.status',{'sessionId':session['id'],'status':'idle'})
fixture.Runtime=ChatRuntime
if __name__=='__main__':
 with tempfile.TemporaryDirectory(prefix='amplifier-chat-ui-') as tmp:
  web.run_app(fixture.main(Path(tmp)),host='127.0.0.1',port=8958,print=None)
