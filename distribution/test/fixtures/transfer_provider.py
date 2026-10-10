"""Offline provider for the complete paired-host graph; no remote requests."""
import hashlib,json
from pathlib import Path
from amplifier_core.models import ProviderInfo
from amplifier_core.message_models import ChatResponse,TextBlock,Usage
class Provider:
    name='fixture'
    def __init__(self,config=None):self.config=config or {}
    def parse_tool_calls(self,response):return response.tool_calls or []
    def get_info(self):return ProviderInfo(id='fixture',display_name='Offline transfer fixture',defaults={'model':'fixture-model','max_tokens':4096},capabilities=['completion:single_attempt:v2'])
    async def list_models(self):return [{'id':'fixture-model'}]
    async def close(self):pass
    async def complete(self,request,**kwargs):
        if (kwargs.get('request_options') or {}).get('single_attempt'):
            with Path(self.config['readinessAudit']).open('a') as output:output.write('one offline request\n')
            prompt=[{'role':'user','content':[{'type':'input_text','text':'Reply with OK.'}]}]
            receipt={'version':2,'model':request.model,'reasoning_effort':request.reasoning_effort,'max_output_tokens':request.max_output_tokens,'timeout_seconds':request.timeout,'native_count_requests':1,'generation_requests':1,'retries':0,'continuations':0,'closed':True,'native_input_tokens':4,'input_sha256':hashlib.sha256(json.dumps(prompt,sort_keys=True,separators=(',',':')).encode()).hexdigest(),'request_sha256':hashlib.sha256(request.model_dump_json().encode()).hexdigest()}
            return ChatResponse(content=[TextBlock(text='OK offline')],finish_reason='stop',metadata={'openai:status':'completed','openai:single_attempt':receipt})
        with Path(self.config['normalAudit']).open('a') as output:output.write('one conversation request\n')
        return ChatResponse(content=[TextBlock(text='Retained native transfer answer.')],finish_reason='stop',usage=Usage(input_tokens=8,output_tokens=5,total_tokens=13))
async def mount(coordinator,config=None):await coordinator.mount('providers',Provider(config),name='fixture')
