import asyncio
from copy import deepcopy

import pytest
from amplifier_foundation.session.metadata import SessionMetadataStore
from amplifier_web.host.storage import SessionStore
from amplifier_web.naming import accept_generated, automatic_metadata, directory_for, read
from amplifier_web.service import AppError, AppService


class NamingRuntime:
    def __init__(self, home):
        self.home = home
        self.started = self.calls = 0
        self.release = asyncio.Event()
        self.ready = asyncio.Event()

    async def start(self, source, emit):
        self.started += 1
        self.source = source
        await emit('runtime.status', {'sessionId': source['id'], 'status': 'starting'})

    async def control(self, identity, operation, arguments):
        assert operation == 'session.naming'
        self.calls += 1
        snapshot = read(directory_for(self.home, self.source))
        self.ready.set()
        await self.release.wait()
        return {**snapshot, 'name': 'A regenerated title', 'description': 'Current conversation'}

    async def close(self): pass


@pytest.fixture
async def named(tmp_path):
    runtime = NamingRuntime(tmp_path)
    app = AppService(tmp_path, runtime, workspace=tmp_path)
    await app.dispatch('session.create', {'title': 'Chosen title'})
    session = app._session()
    app._message(session, 'user', 'Some conversation context')
    SessionStore.for_app(tmp_path, tmp_path).save(session['id'], [{'role': 'user', 'content': 'Some conversation context'}], {})
    yield app, runtime, session
    runtime.release.set()
    await app.close()


async def settled(app, session):
    for _ in range(200):
        if session.get('naming', {}).get('status') != 'working': return
        await asyncio.sleep(.01)
    raise AssertionError('Naming did not settle')


async def test_auto_toggle_is_durable_without_model_call(named, tmp_path):
    app, runtime, session = named
    original = deepcopy(session['messages'])
    await app.dispatch('session.naming', {'id': session['id'], 'automatic': True})
    assert session['autoName'] is True
    assert automatic_metadata(read(directory_for(app.data_dir, session)))
    await app.dispatch('session.naming', {'id': session['id'], 'automatic': False})
    assert not session['autoName']
    assert runtime.started == runtime.calls == 0
    assert session['messages'] == original
    restored = AppService(tmp_path, workspace=tmp_path)
    try: assert restored._session()['autoName'] is False
    finally: await restored.close()


@pytest.mark.parametrize('enabled', [False, True])
async def test_explicit_regeneration_preserves_policy_history_and_status(named, enabled):
    app, runtime, session = named
    await app.dispatch('session.naming', {'id': session['id'], 'automatic': enabled})
    session.update(status='error', error='An earlier failed turn')
    before = deepcopy(session['messages'])
    args = {'id': session['id'], 'regenerate': True}
    await app.dispatch('session.naming', args, command_id='once')
    duplicate = await app.dispatch('session.naming', args, command_id='once')
    assert duplicate['duplicate']
    await runtime.ready.wait()
    with pytest.raises(AppError, match='already'):
        await app.dispatch('session.naming', args, command_id='duplicate-click')
    runtime.release.set()
    await settled(app, session)
    assert session['title'] == 'A regenerated title'
    assert session['autoName'] is enabled
    assert session['status'] == 'error' and session['error'] == 'An earlier failed turn'
    assert session['messages'] == before
    assert runtime.calls == 1


@pytest.mark.parametrize('newer', ['rename', 'policy', 'external'])
async def test_newer_edit_wins_over_delayed_regeneration(named, newer):
    app, runtime, session = named
    await app.dispatch('session.naming', {'id': session['id'], 'regenerate': True})
    await runtime.ready.wait()
    if newer == 'rename':
        await app.dispatch('session.rename', {'id': session['id'], 'title': 'Keep my newer name'})
    elif newer == 'external':
        SessionMetadataStore(directory_for(app.data_dir, session)).set_name('Keep my newer name')
    else:
        await app.dispatch('session.naming', {'id': session['id'], 'automatic': True})
    runtime.release.set()
    await settled(app, session)
    assert session['naming']['status'] == 'conflict'
    assert session['title'] == ('Chosen title' if newer == 'policy' else 'Keep my newer name')


async def test_background_result_respects_auto_policy_and_newer_cli_name(named):
    app, runtime, session = named
    directory = directory_for(app.data_dir, session)
    await app.dispatch('session.naming', {'id': session['id'], 'automatic': True})
    before = read(directory)
    metadata, accepted = accept_generated(directory, {**before, 'name': 'Generated'})
    assert accepted and metadata['name'] == 'Generated'
    SessionMetadataStore(directory).set_name('A CLI name')
    assert not automatic_metadata(read(directory))
    _, accepted = accept_generated(directory, {**read(directory), 'name': 'Unwanted'})
    assert not accepted and read(directory)['name'] == 'A CLI name'


async def test_explicit_suggestion_requests_a_new_name_without_writing(monkeypatch, tmp_path):
    import sys
    from types import SimpleNamespace
    from amplifier_web.host.naming import LiveSessionNaming
    calls = []
    class Config:
        initial_trigger_turn = 2
        update_interval_turns = 5
        def __init__(self, **kwargs): pass
    class Hook:
        def __init__(self, coordinator, config): self.config = config
        async def _generate_name(self, sid, directory, is_update):
            # Upstream update mode only updates the description. Refresh must
            # request initial naming even when there is an existing title.
            calls.append(is_update)
            self._save_metadata(directory, {**self._load_metadata(directory), 'name': 'New suggestion'})
    monkeypatch.setitem(sys.modules, 'amplifier_module_hooks_session_naming',
        SimpleNamespace(SessionNamingHook=Hook, SessionNamingConfig=Config))
    coordinator = SimpleNamespace(session_id='chat', config={'project_dir':str(tmp_path), 'hooks':[{'module':'hooks-session-naming'}]},
        hooks=SimpleNamespace(register=lambda *args, **kwargs: None), register_cleanup=lambda *args: None)
    naming = LiveSessionNaming(coordinator, tmp_path, lambda event: None)
    naming.store.save('chat', [{'role':'user','content':'Context'}], {'name':'Manual original', 'name_source':'manual'})
    before = read(naming.directory)
    result = await naming.suggest()
    assert result['name'] == 'New suggestion'
    assert read(naming.directory) == before
    assert calls == [False]


async def test_same_manual_name_still_disables_auto(named):
    app, runtime, session = named
    await app.dispatch('session.naming', {'id': session['id'], 'automatic': True})
    await app.dispatch('session.rename', {'id': session['id'], 'title': session['title']})
    assert not session['autoName']
    assert not automatic_metadata(read(directory_for(app.data_dir, session)))


async def test_checkpoint_cannot_restore_an_old_policy(named):
    app, runtime, session = named
    store = SessionStore.for_app(app.data_dir, session['workspace'])
    await app.dispatch('session.naming', {'id': session['id'], 'automatic': True})
    rows, stale = store.load(session['id'])
    await app.dispatch('session.naming', {'id': session['id'], 'automatic': False})
    store.save(session['id'], rows, stale)
    assert not automatic_metadata(read(directory_for(app.data_dir, session)))
    store.save(session['id'], rows + [{'role':'assistant','content':'A later checkpoint'}], stale)
    assert not automatic_metadata(read(directory_for(app.data_dir, session)))


async def test_auto_preference_before_first_turn_survives_native_save(tmp_path):
    app = AppService(tmp_path, workspace=tmp_path)
    try:
        await app.dispatch('session.create', {'title':'Named before the first message'})
        session = app._session()
        await app.dispatch('session.naming', {'id':session['id'], 'automatic':True})
        store = SessionStore.for_app(tmp_path, tmp_path)
        store.save(session['id'], [{'role':'user','content':'The first message'}], {})
        assert automatic_metadata(read(directory_for(tmp_path, session)))
    finally:
        await app.close()
