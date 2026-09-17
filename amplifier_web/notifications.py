"""Opt-in browser/ntfy notifications with private delivery credentials."""
import json
from pathlib import Path
import re
from urllib.parse import urlsplit
from .host.config import write_private

class Notifications:
    def __init__(self,home):
        self.path=Path(home)/'config/notifications.json'
        self.sent=set()
    def read(self):
        return json.loads(self.path.read_text()) if self.path.exists() else {'desktop':True,'enabled':False,'server':'https://ntfy.sh','topic':'','token':'','preview':False}
    def public(self):
        value=self.read()
        return {k:v for k,v in value.items() if k not in {'topic','token'}} | {'topicConfigured':bool(value.get('topic')),'tokenConfigured':bool(value.get('token'))}
    def save(self,patch):
        if set(patch)-{'desktop','enabled','server','topic','token','preview'}:raise ValueError('Unknown notification preference')
        value=self.read()
        for key in ('desktop','enabled','preview'):
            if key in patch and type(patch[key]) is not bool:raise ValueError('Notification switches must be true or false')
        value.update(patch)
        url=urlsplit(value['server'])
        if url.scheme!='https' or not url.hostname or url.username or url.password or url.query or url.fragment:raise ValueError('Enter an HTTPS notification server URL')
        if value.get('topic') and not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',value['topic']):raise ValueError('Topics use letters, numbers, dashes and underscores')
        if value.get('enabled') and not value.get('topic'):raise ValueError('Set a notification topic before enabling delivery')
        write_private(self.path,json.dumps(value))
        return self.public()
    async def send(self,session,generation):
        identity=generation.get('generation_id')
        if not identity or identity in self.sent:return
        self.sent.add(identity)
        value=self.read()
        if not value.get('enabled'):return
        message=generation.get('text') if value.get('preview') else 'Your Amplifier response is ready.'
        import aiohttp
        headers={'Authorization':'Bearer '+value['token']} if value.get('token') else {}
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as client:
            async with client.post(value['server'].rstrip('/'),headers=headers,json={'topic':value['topic'],'title':session.get('title','Amplifier'),'message':(message or 'Response ready')[:1000]}) as response:
                if response.status>=400:raise RuntimeError('Notification delivery failed; check the server and credentials')
