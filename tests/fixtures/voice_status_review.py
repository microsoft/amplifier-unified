"""Bounded scripted transport review using the production VoiceCall and policy.

This module is a development fixture, never a shipping route or audio simulation.
"""
import asyncio
import json
from html import escape
from types import SimpleNamespace
from amplifier_web.voice import VoiceCall, VoiceService


class Service:
    def __init__(self):
        self.state={'sessions':[{'id':'review','status':'working','activity':{},'messages':[], 'workers':[]}],
                    'voice':{},'view':{}}
        self.lock=asyncio.Lock();self.calls=[];self.release=asyncio.Event()
    async def record_voice_transcript(self,*args,**kwargs): pass
    async def set_voice_status(self,value):self.state['voice'].update(value)
    async def voice_delegate(self,text,command_id,session_id=None,**kwargs):
        self.calls.append(command_id);return {'accepted':True,'inputId':command_id}
    async def wait_for_response(self,sid,input_id,timeout):
        await self.release.wait()
        return {'generation_id':'scripted-generation','input_ids':[input_id], 'text':'The scripted worker returned its verified result.'}


class Socket:
    closed=False
    def __init__(self,clock):self.events=[];self.clock=clock
    async def send_json(self,event):self.events.append({'observedSeconds':self.clock(),**event})
    async def close(self):self.closed=True


async def scenario(provider, name):
    service=Service();manager=VoiceService(service,http=object());call=VoiceCall(manager,'review');manager.call=call
    now=[0.0];call.clock=lambda:now[0];call.id='scripted-review';call.provider=provider;call.socket=Socket(call.clock)
    manager.request=lambda *a,**kw:asyncio.sleep(0,result=({},'',{}))
    service.state['voice']={'id':call.id,'sessionId':'review','status':'connected'}
    steps=[]
    async def tick(when, description):
        now[0]=when;await call.progress_tick();steps.append({'at':when,'description':description,'wireCount':len(call.socket.events)})
    if name=='short':
        await tick(0,'Work starts');await tick(4,'Still inside grace')
        service.state['sessions'][0]['status']='idle';await tick(4.5,'Work settles before grace')
        assert call.socket.events==[]
    elif name=='long':
        for when in (0,5,20,60,120,240):
            call.realtime_responding=False
            await tick(when,'Observe unchanged work')
        assert len([row for row in call.socket.events if row['type'] in {'session.commentary.append','response.create'}])==4
    elif name=='phases':
        await tick(0,'Work starts')
        service.state['sessions'][0]['activity']={'activeTools':[{'tool':'bash'}]}
        await tick(4,'Command phase begins without resetting grace')
        await tick(5,'First truthful phase notice');call.realtime_responding=False
        service.state['sessions'][0]['questions']=[{'status':'pending'}]
        await tick(6,'Question arrives during cooldown');await tick(10,'Attention notice after cooldown')
    elif name=='status':
        await call.record('user','Are you still working?','u1')
        if provider=='live':await call.delegate_live('status')
        else:await call.realtime_tool({'call_id':'status','name':'amplifier_delegate','arguments':'{"text":"Are you still working?"}'})
        assert service.calls==[]
        steps.append({'at':0,'description':'Immediate current observation; zero work inputs','wireCount':len(call.socket.events)})
    elif name=='interruption':
        await tick(0,'Work starts');call.realtime_playing=True
        await tick(5,'Playback blocks unsent notice')
        assert not call.socket.events and call.pending_progress
        await call.handle({'type':'input_audio_buffer.speech_started'})
        call.realtime_playing=False;await call.flush_progress()
        assert not call.socket.events and call.pending_progress is None
        steps.append({'at':5,'description':'User speech discards unsent progress','wireCount':0})
    elif name in {'completion','hangup'}:
        await call.record('user','Run the scripted task.','work')
        task=asyncio.create_task(call.delegate_live('work') if provider=='live' else call.realtime_tool({
            'call_id':'work','name':'amplifier_delegate','arguments':'{"text":"Run the scripted task."}'}))
        await asyncio.sleep(0);assert len(service.calls)==1 and not task.done()
        await tick(0,'One work input accepted');call.realtime_playing=True
        await tick(5,'Wait is queued behind playback')
        if name=='hangup':
            call.final.set();await call.close();assert not task.done()
        service.release.set();await task
        assert call.pending_progress is None
        if name=='hangup':assert not call.socket.events
        else:
            call.realtime_playing=False;await call.flush_realtime_response()
            assert 'verified result' in json.dumps(call.socket.events)
            assert 'Host progress observation' not in json.dumps(call.socket.events)
        steps.append({'at':5,'description':'Accepted work finishes; '+('media remains closed' if name=='hangup' else 'verified result wins'), 'wireCount':len(call.socket.events)})
    else:raise ValueError('Unknown scripted scenario')
    return {'id':name,'passed':True,'steps':steps,'acceptedInputs':len(service.calls), 'events':call.socket.events}


async def review(provider='live'):
    if provider not in {'live','realtime'}:raise ValueError('Unknown transport')
    return {'schemaVersion':1,'kind':'scripted-protocol-review','provider':provider,
            'limitations':['No microphone, speakers or provider network used','Timestamps are a controlled clock, not audible latency'],
            'policy':{'graceSeconds':5,'phaseCooldownSeconds':5,'elapsedThresholdSeconds':[20,60,120],
                      'unchangedAfterFinalThreshold':'quiet','pendingProgressSlots':1},
            'scenarios':[await scenario(provider,name) for name in ('short','long','phases','status','interruption','completion','hangup')]}


def review_html(results):
    """Inert owner review artifact: escaped text, no scripts or network actions."""
    sections=[]
    titles={'short':'Quick work stays quiet','long':'Long work gets finite updates',
            'phases':'Phase changes keep the original grace','status':'A direct status question',
            'interruption':'Your voice takes priority','completion':'A verified result wins',
            'hangup':'Work continues after hangup'}
    for provider, result in results.items():
        cards=[]
        for scenario in result['scenarios']:
            steps=''.join('<li><b>'+escape(str(step['at']))+'s</b> '+escape(step['description'])+'</li>' for step in scenario['steps'])
            notices=[]
            for event in scenario['events']:
                text=event.get('text') or event.get('content')
                item=event.get('item',{})
                if not text and item.get('type')=='function_call_output':text=item.get('output')
                if not text and item.get('content'):text=' '.join(part.get('text','') for part in item['content'])
                if text:notices.append('<li><b>'+escape(str(event['observedSeconds']))+'s</b> '+escape(text)+'</li>')
            cards.append('<article><h3>'+escape(titles[scenario['id']])+'</h3><p class="pass">Passed · '+str(scenario['acceptedInputs'])+' accepted work inputs</p><ol>'+steps+'</ol><h4>Captured notices</h4>'+
                         ('<ul>'+''.join(notices)+'</ul>' if notices else '<p>No notice sent.</p>')+
                         '<details><summary>Transport events</summary><pre>'+escape(json.dumps(scenario['events'],indent=2))+'</pre></details></article>')
        sections.append('<section id="'+escape(provider)+'"><h2>'+escape(provider.title())+'</h2>'+''.join(cards)+'</section>')
    return '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Voice pacing review</title><style>body{font:17px/1.55 system-ui;background:#f4f5fa;color:#18233b;margin:0}main{max-width:980px;margin:40px auto;padding:0 24px}header,article{background:white;border:1px solid #dce1ee;border-radius:16px;padding:24px;margin:20px 0}h1,h2,h3{line-height:1.2}.note{background:#fff3d7;padding:16px;border-radius:10px}.pass{color:#286443}a{color:#4a38bb}li{margin:8px 0}pre{overflow:auto;font-size:12px;white-space:pre-wrap;overflow-wrap:anywhere}nav{display:flex;gap:24px}b{font-variant-numeric:tabular-nums}</style><main><header><h1>Voice pacing review</h1><p>Development preview · unmerged #36</p><p class="note"><strong>Controlled clock, captured messages.</strong> These scenarios exercise the actual call and pacing code. They do not use a microphone, speakers or a provider connection, and do not demonstrate audible timing.</p><p>Five seconds of grace; a five-second cooldown for phase changes; distinct observations at twenty seconds, one minute and two minutes, then quiet. Deferred updates coalesce. Your speech and verified results take priority.</p><nav><a href="#live">Live scenarios</a><a href="#realtime">Realtime scenarios</a><a href="/">Open the test app</a></nav></header>'+''.join(sections)+'</main></html>'


if __name__=='__main__':
    print(json.dumps(asyncio.run(review())))
