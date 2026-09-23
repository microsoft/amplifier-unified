"""Host-owned voice credentials and a short, isolated voice sample.

Samples use the selected conversation model, with no microphone, user messages,
tools or delegation backend. Audio is returned to the requesting client only.
"""
import asyncio
import base64
import io
import os
import time
import wave

import aiohttp

from .preferences import SettingsStore
from .updates import work_paused
from .voice_options import voices_for

SAMPLE = 'Hello! This is how I sound. I am ready to help you with your work.'
PRIVATE_KEY = 'AMPLIFIER_VOICE_API_KEY'


def definitions(schema, string):
    return {
        'voice.configuration': ('Read voice availability and credential source names, never key values.', schema()),
        'voice.configure': ('Choose the host OpenAI environment key or save a separate private key for voice.', schema(
            {'source': {'enum': ['environment', 'private']}, 'apiKey': string(4000)}, ['source'])),
        'voice.preview': ('Generate a short audible sample with the selected voice model. No microphone or conversation is sent.', schema(
            {'model': {'enum': ['gpt-live-1', 'gpt-realtime-2.1']}, 'voice': string(100)}, ['model', 'voice'])),
    }


def key_source(service):
    if not hasattr(service,'data_dir'): return 'environment'
    settings = SettingsStore(service.data_dir).read(service.default_workspace)
    return settings.get('voice', {}).get('credential_source', 'environment')


def configuration(manager):
    return {'available': bool(manager.api_key), 'source': manager.credential_source,
            'environmentAvailable': bool(os.environ.get('OPENAI_API_KEY')),
            'privateKeyAvailable': bool(os.environ.get(PRIVATE_KEY)),
            'reason': None if manager.api_key else 'Add an OpenAI API key in Voice settings to enable calls and previews.'}


async def dispatch(service, action, args):
    from .voice import VoiceError
    manager = service.voice_service
    if manager is None:
        raise VoiceError('Voice is unavailable on this installation.', 409, 'unavailable')
    if action == 'voice.preview':
        return await preview(manager, args['model'], args['voice'])
    if action == 'voice.configure':
        async with manager.lock:
            if manager.call and not manager.call.closed or service.state.get('voicePreviewBusy'):
                raise VoiceError('Finish the active call or preview before changing its key.', 409)
            source = args['source']
            value = args.get('apiKey', '').strip()
            if source == 'environment' and not os.environ.get('OPENAI_API_KEY'):
                raise VoiceError('No OPENAI_API_KEY is available to this Amplifier host.', 409)
            if source == 'private' and not value and not os.environ.get(PRIVATE_KEY):
                raise VoiceError('Enter an OpenAI API key.', 400)
            if source == 'private' and value:
                from .setup import SetupManager
                await asyncio.to_thread(SetupManager(service.data_dir)._keys, {PRIVATE_KEY: value})
            store = SettingsStore(service.data_dir)
            await asyncio.to_thread(store.update, service.default_workspace, 'global',
                                    lambda settings: settings.setdefault('voice', {}).update(credential_source=source))
    manager.credential_source = key_source(service)
    result = configuration(manager)
    async with service.lock:
        service.state['voiceConfiguration'] = result
        service._publish()
    return result


def wav_audio(pcm):
    if len(pcm) < 4800:
        raise ValueError('The voice provider returned no usable audio. Try again.')
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(24000)
        output.writeframes(pcm[:len(pcm)//2*2])
    return base64.b64encode(buffer.getvalue()).decode()


async def render_sample(manager, model, voice):
    """Bound provider time, message size, audio length and silence-clock lifetime."""
    live = model == 'gpt-live-1'
    url = 'wss://api.openai.com/v1/live/sessions' if live else 'wss://api.openai.com/v1/realtime?model='+model
    if manager.http is None:
        manager.http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
    async with manager.http.ws_connect(url, headers=manager.headers, max_msg_size=2*1024*1024,
                                       timeout=aiohttp.ClientWSTimeout(ws_close=2)) as socket:
        instruction = 'Speak now in English. Say only: "'+SAMPLE+'". Then remain silent. Do not delegate.'
        if live:
            await socket.send_json({'type':'session.start', 'session':{
                'model':model, 'instructions':'You are demonstrating a voice. Never delegate.',
                'audio':{'format':{'type':'audio/pcm','rate':24000},'output':{'voice':voice}},
                'delegation':{'type':'client'},'store':False}})
        else:
            await socket.send_json({'type':'session.update', 'session':{
                'type':'realtime','model':model,'output_modalities':['audio'],
                'audio':{'input':{'turn_detection':None},'output':{'voice':voice,'format':{'type':'audio/pcm','rate':24000}}},
                'tools':[],'tool_choice':'none'}})
        clock = None
        audio = bytearray()
        started = False
        sample_start = None
        async def silence():
            chunk = base64.b64encode(bytes(4800)).decode()  # 100 ms, paced in real time.
            while True:
                await socket.send_json({'type':'session.input_audio.append','audio':chunk})
                await asyncio.sleep(.1)
        try:
            async with asyncio.timeout(24):
                while len(audio) < 12*48000:
                    event = await socket.receive_json(timeout=15)
                    kind = event.get('type')
                    if kind == 'error':
                        raise ValueError('The voice provider could not create this sample. Check model access and your API key.')
                    if not started and kind == ('session.started' if live else 'session.updated'):
                        started = True
                        if live:
                            clock = asyncio.create_task(silence())
                            await socket.send_json({'type':'session.instructions.append','event_id':'voice_sample','delegation_id':None,'content':instruction})
                        else:
                            await socket.send_json({'type':'response.create','response':{'instructions':instruction,'max_output_tokens':200}})
                    if kind == ('session.output_audio.delta' if live else 'response.output_audio.delta'):
                        chunk = base64.b64decode(event.get('delta',''), validate=True)
                        if sample_start is None: sample_start = time.monotonic()
                        audio.extend(chunk)
                        if live and time.monotonic()-sample_start >= 10:
                            break
                    if kind == 'response.done' and not live:
                        if event.get('response',{}).get('status') not in {None,'completed'}:
                            raise ValueError('The voice sample did not finish. Check your API account and try again.')
                        break
                    if kind in {'session.closed','session.delegation.created'}:
                        break
        finally:
            if clock:
                clock.cancel()
                await asyncio.gather(clock, return_exceptions=True)
            if live and started and not socket.closed:
                await socket.send_json({'type':'session.close'})
                try:
                    async with asyncio.timeout(2):
                        while (await socket.receive_json()).get('type') != 'session.closed': pass
                except (TimeoutError, aiohttp.ClientError, TypeError):
                    pass
        return wav_audio(audio[:12*48000])


async def preview(manager, model, voice):
    from .voice import VoiceError
    if voice not in voices_for(model):
        raise VoiceError('Choose a voice available for this model.', 400)
    async with manager.lock:
        async with manager.service.lock:
            if not manager.api_key:
                raise VoiceError('Add an OpenAI API key in Voice settings first.', 409)
            if work_paused(manager.service.state):
                raise VoiceError('An update is activating. Try the preview when it finishes.', 409)
            if manager.call and not manager.call.closed or manager.service.state.get('voicePreviewBusy'):
                raise VoiceError('Finish the current call or preview first.', 409)
            manager.service.state['voicePreviewBusy'] = True
            manager.service._publish()
    try:
        try:
            audio = await render_sample(manager, model, voice)
        except (aiohttp.ClientError, TimeoutError, ValueError, TypeError) as exc:
            # Provider payloads and auth headers never enter state or errors.
            raise VoiceError('Could not preview this voice. Check the OpenAI key, model access and connection, then try again.', 502) from None
        return {'model':model,'voice':voice,'mimeType':'audio/wav','audio':audio}
    finally:
        async with manager.service.lock:
            manager.service.state['voicePreviewBusy'] = False
            manager.service._publish()
