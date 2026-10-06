"""Installed passive native-activity browser fixture. No model or audio calls."""
import asyncio
import json
import os
from pathlib import Path
import signal
import socket
import sys
import uuid


async def main():
    root = Path(sys.argv[1]).resolve()
    root.mkdir(parents=True, exist_ok=False, mode=0o700)
    workspace = root / 'workspace'
    workspace.mkdir()
    os.environ.update(AMPLIFIER_HOME=str(root / 'native'), AMPLIFIER_WEB_HOME=str(root / 'app'),
        AMPLIFIER_UNIFIED_IMPORT_HOME=str(root / 'legacy'),
        AMPLIFIER_SESSION_STATE_HOME=str(root / 'ownership'),
        AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH=str(root / 'capture'))
    from aiohttp import web
    from amplifier_web.server import create_app
    from amplifier_web.deployment import validate_server
    from amplifier_web.host.storage import SessionStore
    from amplifier_web.session_files import project_slug, sessions_dir
    from amplifier_web.voice_messages import voice_provenance
    import amplifier_web.server as implementation
    assert '/site-packages/' in implementation.__file__
    stopped = asyncio.Event()
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, stopped.set)
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    sock.listen(128)
    sock.setblocking(False)
    port = sock.getsockname()[1]
    origin = f'http://127.0.0.1:{port}'
    config = validate_server({'bind': ['127.0.0.1'], 'port': port, 'public_origins': [origin]})
    runner = None
    try:
        app = await create_app(root / 'app', workspace=workspace, voice=False,
            background_updates=False, preload_providers=False, server_config=config)
        service = app['service']
        sid = str(uuid.uuid4())
        await service.dispatch('session.create', {'id': sid, 'title': 'Saved voice activity fixture',
                                                 'workspace': str(workspace)}, include_state=False)
        rows = [
            {'role': 'user', 'content': 'PRIVATE-HANDOFF-SENTINEL', 'metadata': {
                'amplifier_input': voice_provenance('voice:fixture:answer')}},
            {'role': 'assistant', 'content': '', 'tool_calls': [{'id': 'tool-with-answer',
                'type': 'function', 'function': {'name': 'inspect', 'arguments': '{}'}}]},
            {'role': 'tool', 'tool_call_id': 'tool-with-answer', 'content': 'PRIVATE-TOOL-SENTINEL'},
            {'role': 'assistant', 'content': 'Public saved answer'},
            {'role': 'user', 'content': 'PRIVATE-HANDOFF-SENTINEL', 'metadata': {
                'amplifier_input': voice_provenance('voice:fixture:empty')}},
            {'role': 'assistant', 'content': '', 'tool_calls': [{'id': 'tool-no-answer',
                'type': 'function', 'function': {'name': 'inspect', 'arguments': '{}'}}]},
            {'role': 'tool', 'tool_call_id': 'tool-no-answer', 'content': 'PRIVATE-TOOL-SENTINEL'},
        ]
        store = SessionStore(sessions_dir(workspace), shared=True)
        store.save(sid, rows, {'working_dir': str(workspace), 'bundle_name': 'fixture'})
        transcript = store.directory(sid) / 'transcript.jsonl'
        before = transcript.read_bytes()
        capture = root / 'capture' / project_slug(workspace) / 'sessions' / sid / 'context-intelligence'
        capture.mkdir(parents=True)
        events = [{'event': event, 'session_id': sid, 'timestamp': '2026-01-01T12:00:00Z',
                   'data': {'tool_call_id': call, 'tool_name': 'inspect'}}
                  for call in ('tool-with-answer', 'tool-no-answer')
                  for event in ('tool:pre', 'tool:post')]
        (capture / 'events.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in events))
        session = service._session(sid)
        session.update(nativeProject=project_slug(workspace), nativeIdentity=sid, runtimeSessionId=sid)
        await service.history.load(sid)
        assert len(session['execution']['nodes']) == 2
        service._publish()
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.SockSite(runner, sock)
        await site.start()
        assert site._server.sockets[0].getsockname()[1] == app['server_config']['port'] == port
        print(json.dumps({'origin': origin, 'sessionId': sid, 'controlToken': app['control_token'],
                          'pid': os.getpid()}), flush=True)
        await stopped.wait()
    finally:
        if runner:
            await runner.cleanup()
            (root / 'final.json').write_text(json.dumps({
                'canonical_unchanged': transcript.read_bytes() == before,
                'runtime_workers': len(getattr(service.runtime, 'workers', {})),
                'fixture_model_inputs': 0,
            }, indent=2))
        sock.close()


asyncio.run(main())