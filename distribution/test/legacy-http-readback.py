"""Exercise actual old-app HTTP readback of post-candidate writes, offline."""
from pathlib import Path
import asyncio
import hashlib
import json
import os
import sys
import uuid

legacy, root, expected_messages = sys.argv[1:]
legacy, root = Path(legacy).resolve(), Path(root).resolve()
expected_messages = int(expected_messages)
assert legacy.is_dir() and root.is_dir() and root.name.startswith('legacy-continuation-')
home = root / 'rollback-native'
workspace = root / 'workspace'
app_home = root / ('legacy-http-app-' + str(uuid.uuid4()))
for key in list(os.environ):
    if key.startswith(('AMPLIFIER_', 'OPENAI_', 'ANTHROPIC_', 'AZURE_', 'GOOGLE_', 'PYTHON', 'UV_')):
        os.environ.pop(key)
os.environ.update(AMPLIFIER_HOME=str(home), AMPLIFIER_WEB_HOME=str(app_home),
                  AMPLIFIER_SESSION_STATE_HOME=str(root/'legacy-http-writers'),
                  AMPLIFIER_SOURCE_STORE=str(root/'legacy-http-sources'),
                  XDG_CACHE_HOME=str(root/'legacy-http-cache'), PYTHONDONTWRITEBYTECODE='1')
sys.path.insert(0, str(legacy))
external = []
children = []
def audit(event, args):
    if event == 'socket.connect':
        address = args[1]
        if isinstance(address, tuple) and address[0] not in {'127.0.0.1', '::1', 'localhost'}:
            external.append(str(address[0]))
            raise RuntimeError('External connection forbidden in rollback qualification')
    if event == 'subprocess.Popen':
        children.append({'executable': args[0], 'arguments': args[1]})
        raise RuntimeError('Execution forbidden in passive rollback qualification')
sys.addaudithook(audit)
from aiohttp import web, ClientSession
from amplifier_web.server import create_app
from amplifier_web.auth import control_token
from amplifier_web.deployment import load_server_config

def hashes():
    return {str(p.relative_to(home)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in home.rglob('*') if p.is_file()}

async def main():
    before = hashes()
    requests = (root/'provider-requests.jsonl').read_bytes()
    effects = (root/'effects.jsonl').read_bytes()
    config = load_server_config(app_home)
    config['runtime']['prewarm_on_select'] = False
    app = await create_app(app_home, workspace=str(workspace), voice=False,
                           background_updates=False, preload_providers=False, server_config=config)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    origin = 'http://127.0.0.1:' + str(site._server.sockets[0].getsockname()[1])
    try:
        async with ClientSession(headers={'Authorization': 'Bearer ' + control_token(app_home)}) as client:
            client_id = str(uuid.uuid4())
            async with client.post(origin+'/api/clients/attach', json={
                    'clientId': client_id, 'kind': 'web', 'protocolVersion': 1}) as response:
                attached = await response.json()
                assert response.status == 200, attached
            client.headers['X-Amplifier-Client'] = client_id
            async def get(path, **params):
                async with client.get(origin+path, params=params) as response:
                    data = await response.json()
                    assert response.status == 200, (path, response.status, data)
                    return data
            health = await get('/api/health')
            assert health['app'] == 'amplifier-unified'
            saved = None
            for _ in range(100):
                state = await get('/api/state')
                saved = next((s for s in state['sessions'] if s.get('nativeIdentity') == 'd171ccd8-a11a-4c92-94f6-65054bda043b' or s['id'] == 'd171ccd8-a11a-4c92-94f6-65054bda043b'), None)
                if saved: break
                await asyncio.sleep(.1)
            (root/'legacy-http-initial.json').write_text(json.dumps(state, indent=2))
            assert saved, 'Restored native conversation was not discovered'
            async with client.post(origin+'/api/actions', json={
                    'action': 'session.select', 'args': {'id': saved['id']},
                    'id': 'readback-select-existing-conversation'}) as response:
                selection = await response.json()
                assert response.status == 200, selection
            selected = (await get('/api/sessions/'+saved['id']))['session']
            (root/'legacy-http-state.json').write_text(json.dumps(state, indent=2))
            for _ in range(100):
                page = await get('/api/conversation/detail', sessionId=saved['id'], part='messages')
                if page['items']: break
                await asyncio.sleep(.1)
            native_pages = 1
            for _ in range(20):
                selected = (await get('/api/sessions/'+saved['id']))['session']
                offset = selected.get('sharedHistoryOffset', 0)
                if not offset: break
                async with client.post(origin+'/api/actions', json={
                        'action': 'session.history', 'args': {'id': saved['id'], 'before': offset, 'limit': 100},
                        'id': 'readback-history-' + str(offset)}) as response:
                    loaded = await response.json()
                    assert response.status == 200, loaded
                for _ in range(100):
                    selected = (await get('/api/sessions/'+saved['id']))['session']
                    if selected.get('sharedHistoryOffset', offset) < offset: break
                    await asyncio.sleep(.1)
                assert selected.get('sharedHistoryOffset', offset) < offset
                native_pages += 1
            assert selected.get('sharedHistoryOffset', 0) == 0
            page = await get('/api/conversation/detail', sessionId=saved['id'], part='messages')
            messages = page['items']; pages = 1
            while page.get('before'):
                page = await get('/api/conversation/detail', sessionId=saved['id'], part='messages', before=page['before'])
                messages = page['items'] + messages; pages += 1
            texts = [row['text'] for row in messages]
            assert texts[0] == 'Keep the project phrase violet compass.', texts[:2]
            assert texts[-1] == 'Continued with violet compass and saved the new artifact.', texts[-2:]
            assert sum('CONTINUE-MIGRATED-41' in text for text in texts) == 1
            assert len(texts) == expected_messages
            assert selected['title'] == 'Retained continuation fixture'
            result = {'kind': 'legacy-http-post-candidate-readback', 'passed': True,
                      'legacyVersion': health.get('version'), 'session': saved['id'],
                      'visibleMessages': len(texts), 'httpPages': pages, 'nativePages': native_pages, 'authenticated': True,
                      'externalConnections': external, 'childExecutions': children,
                      'limits': ['Actual old app and authenticated HTTP, using an isolated restored native copy.',
                                 'Old app configured with prewarm_on_select disabled for passive readback; its default preparation is a separate execution acceptance gate.',
                                 'Fresh legacy application state; not a full all-owner installation transition or physical browser acceptance.']}
    finally:
        await runner.cleanup()
    assert (root/'provider-requests.jsonl').read_bytes() == requests
    assert (root/'effects.jsonl').read_bytes() == effects
    after = hashes()
    assert all(after.get(name) == sha for name, sha in before.items()), 'Old-app startup changed existing restored native bytes'
    added = sorted(after.keys() - before.keys())
    # Main records its own host-service diagnostics on startup. These are not
    # conversation turns; retain and report them rather than hiding the writes.
    for name in added:
        parts = Path(name).parts
        assert (len(parts) == 6 and parts[0] == 'projects' and parts[2] == 'sessions'
                and parts[3].startswith('unified-') and parts[4] == 'context-intelligence'
                and parts[5] in {'metadata.json', 'events.jsonl', '.unified-append.lock'}), name
    assert not external and not children, {'externalAttempts': external, 'childAttempts': children}
    result.update(existingNativeFilesUnchanged=len(before), legacyServiceDiagnosticFilesAdded=added, workReplayed=False)
    (root/'legacy-http-readback.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result))

asyncio.run(main())
