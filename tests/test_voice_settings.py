"""Voice setup and samples use shared actions without touching conversation data."""
import asyncio
import base64
import io
import json
import wave
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from amplifier_web.service import AppService, AppError
from amplifier_web.voice import VoiceService, VoiceError
from amplifier_web import voice_settings as settings
from amplifier_web.updates import UpdateManager

class Runtime:
    async def close(self): pass

@pytest.fixture
async def app(tmp_path, monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY','host-original-key')
    monkeypatch.delenv(settings.PRIVATE_KEY, raising=False)
    service=AppService(tmp_path/'app',Runtime(),workspace=tmp_path)
    service.voice_service=VoiceService(service)
    yield service
    await service.close()

async def test_credential_choice_preserves_host_key_and_survives_restart(app,monkeypatch):
    from amplifier_web.host.config import _KEY_FILE_VALUES
    monkeypatch.setitem(_KEY_FILE_VALUES,settings.PRIVATE_KEY,'')
    result=await app.dispatch('voice.configuration',{})
    assert result['result']['environmentAvailable'] and result['result']['available']
    await app.dispatch('voice.configure',{'source':'private','apiKey':'separate-voice-key'})
    assert app.voice_service.api_key=='separate-voice-key'
    import os
    assert os.environ['OPENAI_API_KEY']=='host-original-key'
    restarted=VoiceService(app)
    assert restarted.credential_source=='private' and restarted.api_key=='separate-voice-key'
    await app.dispatch('voice.configure',{'source':'environment'})
    assert app.voice_service.api_key=='host-original-key'
    public=json.dumps(app.get_state())
    assert 'host-original-key' not in public and 'separate-voice-key' not in public

async def test_sample_does_not_create_history_and_fences_calls_and_updates(app,monkeypatch):
    before=list(app.state['sessions']);entered=asyncio.Event();release=asyncio.Event()
    async def render(*args):
        entered.set();await release.wait();return 'sample'
    monkeypatch.setattr(settings,'render_sample',render)
    task=asyncio.create_task(app.dispatch('voice.preview',{'model':'gpt-live-1','voice':'willow'}))
    await entered.wait()
    assert UpdateManager(app).busy()
    with pytest.raises(AppError,match='preview'):
        await app.dispatch('call.start',{})
    with pytest.raises(AppError,match='preview'):
        await app.dispatch('voice.preview',{'model':'gpt-live-1','voice':'marin'})
    release.set();result=await task
    assert result['result']['audio']=='sample'
    assert not app.state['voicePreviewBusy'] and app.state['sessions']==before
    assert 'audio' not in app.state['voiceConfiguration'] if 'voiceConfiguration' in app.state else True

async def test_failed_sample_cleans_up_and_never_exposes_provider_payload(app,monkeypatch):
    async def fail(*args):raise ValueError('secret provider payload')
    monkeypatch.setattr(settings,'render_sample',fail)
    with pytest.raises(AppError,match='Could not preview') as error:
        await app.dispatch('voice.preview',{'model':'gpt-realtime-2.1','voice':'sage'})
    assert 'secret' not in str(error.value)
    assert not app.state['voicePreviewBusy']
    with pytest.raises(AppError,match='available for this model'):
        await app.dispatch('voice.preview',{'model':'gpt-realtime-2.1','voice':'willow'})

class Socket:
    closed=False
    def __init__(self,events): self.events=iter(events);self.sent=[]
    async def __aenter__(self):return self
    async def __aexit__(self,*args):self.closed=True
    async def send_json(self,event):self.sent.append(event)
    async def receive_json(self,**kwargs):return next(self.events)

@pytest.mark.parametrize('model,voice',[('gpt-live-1','willow'),('gpt-realtime-2.1','sage')])
async def test_preview_protocol_uses_exact_model_voice_and_bounded_audio(model,voice):
    live=model=='gpt-live-1';pcm=b'\x00\x10'*4800
    events=[{'type':'session.started' if live else 'session.updated'},
            {'type':'session.output_audio.delta' if live else 'response.output_audio.delta','delta':base64.b64encode(pcm).decode()},
            {'type':'session.closed' if live else 'response.done','response':{'status':'completed'}}, {'type':'session.closed'}]
    socket=Socket(events);calls=[]
    def connect(url,**kwargs):calls.append((url,kwargs));return socket
    manager=SimpleNamespace(http=SimpleNamespace(ws_connect=connect),headers={'Authorization':'Bearer synthetic'})
    sample=await settings.render_sample(manager,model,voice)
    request=socket.sent[0]['session'];assert request['model']==model and request['audio']['output']['voice']==voice
    assert ('/live/sessions' in calls[0][0]) is live
    assert 'microphone' not in json.dumps(socket.sent) and 'conversation' not in json.dumps(socket.sent)
    with wave.open(io.BytesIO(base64.b64decode(sample))) as audio:
        assert audio.getframerate()==24000 and audio.getnchannels()==1 and audio.readframes(4800)==pcm
    assert socket.closed

async def test_repeating_same_model_and_voice_reuses_short_sample(app, monkeypatch):
    render = AsyncMock(return_value='fixture-audio')
    monkeypatch.setattr(settings, 'render_sample', render)
    for _ in range(2):
        await app.dispatch('voice.preview', {'model':'gpt-live-1', 'voice':'marin'})
    assert render.await_count == 1
    await app.dispatch('voice.preview', {'model':'gpt-realtime-2.1', 'voice':'marin'})
    assert render.await_count == 2
    monkeypatch.delenv('OPENAI_API_KEY')
    with pytest.raises(AppError, match='API key'):
        await app.dispatch('voice.preview', {'model':'gpt-live-1', 'voice':'marin'})

async def test_live_preview_trims_startup_silence_and_stops_after_speech():
    speech=b'\x00\x10'*12000
    delta=lambda chunk:{'type':'session.output_audio.delta','delta':base64.b64encode(chunk).decode()}
    socket=Socket([{'type':'session.started'},delta(bytes(48000)),delta(speech),delta(bytes(28800)),{'type':'session.closed'}])
    manager=SimpleNamespace(http=SimpleNamespace(ws_connect=lambda *a,**k:socket),headers={})
    result=await settings.render_sample(manager,'gpt-live-1','marin')
    with wave.open(io.BytesIO(base64.b64decode(result))) as audio:
        assert audio.getnframes() == (4800+len(speech)+5760)//2
    assert socket.sent[-1]['type']=='session.close'

async def test_live_preview_rejects_silent_audio():
    socket=Socket([{'type':'session.started'},{'type':'session.output_audio.delta','delta':base64.b64encode(bytes(48000)).decode()},{'type':'session.closed'},{'type':'session.closed'}])
    manager=SimpleNamespace(http=SimpleNamespace(ws_connect=lambda *a,**k:socket),headers={})
    with pytest.raises(ValueError,match='No speech'):
        await settings.render_sample(manager,'gpt-live-1','marin')
