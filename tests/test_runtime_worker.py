"""Worker-stamped app arguments cross the service boundary, not just query_history."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from jsonschema import ValidationError

from amplifier_web.collaboration import BINDING, PRINCIPAL
from amplifier_web.runtime_worker import Worker
from amplifier_web.service import AppError, AppService


TRANSPORT_FIELDS = {'_generationId', '_runtimeSessionId', '_inputBindings', '_inputClients'}


@pytest.fixture
async def worker_bridge(tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_HOME', str(tmp_path / 'amplifier'))
    app = AppService(tmp_path / 'app', workspace=tmp_path)
    root = app._new_session({'title': 'Earlier decision', 'workspace': str(tmp_path)})
    root['runtimeSessionId'] = 'native-root'
    root['collaborationGeneration'] = {'id': 'generation-root', 'inputIds': ['input-1']}
    root['messages'] = [{'id': 'message-1', 'role': 'user', 'text': 'A retained needle decision'}]
    app.state['sessions'].append(root)
    app.state['selectedSessionId'] = root['id']
    for client in ('old-browser', 'input-browser'):
        app.clients.attach(client)
    monkeypatch.setattr(app.history, 'refresh', AsyncMock())
    worker = Worker.__new__(Worker)
    worker.session = SimpleNamespace(coordinator=SimpleNamespace(session_id='native-root'))
    worker.runtime = SimpleNamespace(generation={'id': 'generation-root'})
    worker.context_inputs = ['input-1']
    worker.context_bindings = {
        'input-1': {'clientId': 'input-browser', 'targets': [{'surfaceId': 'surface-1'}]}}
    envelopes = []

    async def transport(operation, args):
        envelopes.append(deepcopy(args))
        # A callback may have inherited an older browser request's context.
        with app.clients.bind('old-browser'):
            return await app.app_bridge(operation, args, root['id'])

    worker.bridge = transport
    try:
        yield app, root, worker, envelopes
    finally:
        await app.close()


@pytest.mark.parametrize('action', ['list', 'search', 'read'])
async def test_actual_worker_history_accepts_transport_but_validates_public_args(worker_bridge, monkeypatch, action):
    from amplifier_web import history_query
    app, root, worker, envelopes = worker_bridge
    public = {'action': action, 'limit': 1}
    if action == 'search':
        public['query'] = 'needle'
    elif action == 'read':
        public['session_id'] = root['id']
    before = deepcopy(root)
    query = history_query.query_history

    async def inspect(service, args, caller):
        assert args == public
        assert not TRANSPORT_FIELDS.intersection(args)
        assert PRINCIPAL.get() == 'native-root'
        assert BINDING.get() == envelopes[-1]
        binding, active = service.collaboration.active_binding(root)
        assert binding['_generationId'] == active['id'] == 'generation-root'
        assert service.clients.current.get() == 'input-browser'
        return await query(service, args, caller)

    monkeypatch.setattr(history_query, 'query_history', inspect)
    forged = {'_runtimeSessionId': 'foreign-root', '_generationId': 'foreign-generation',
              '_inputBindings': [{'inputId': 'forged', 'clientId': 'old-browser'}],
              '_inputClients': ['old-browser']}
    supplied = {**public, **forged}
    result = await worker.app_access_bridge(worker.session.coordinator)('history', supplied)
    assert envelopes[-1] == {
        **public, '_runtimeSessionId': 'native-root', '_generationId': 'generation-root',
        '_inputBindings': [{'inputId': 'input-1', 'clientId': 'input-browser'}],
        '_inputClients': ['input-browser']}
    if action == 'read':
        assert result['messages'][0]['text'] == 'A retained needle decision'
    else:
        assert result['items'][0]['id'] == 'native-root'
        if action == 'search':
            assert result['items'][0]['matches'][0]['message_id'] == 'message-1'
    assert supplied == {**public, **forged}
    assert root == before
    assert PRINCIPAL.get() is None and BINDING.get() is None
    assert app.clients.current.get() is None


@pytest.mark.parametrize('extra', ['unexpected', '_unrecognizedTransport', '_contextBindings'])
async def test_actual_worker_history_still_rejects_unknown_public_and_private_fields(worker_bridge, extra):
    app, root, worker, envelopes = worker_bridge
    with pytest.raises(ValidationError, match='Additional properties'):
        await worker.app_access_bridge(worker.session.coordinator)('history', {'action': 'list', extra: []})
    assert extra in envelopes[-1]
    assert PRINCIPAL.get() is None and BINDING.get() is None
    assert app.clients.current.get() is None


async def test_worker_dispatch_keeps_observation_provenance_outside_public_args(worker_bridge, monkeypatch):
    app, root, worker, envelopes = worker_bridge
    expected = [{'inputId': 'input-1', 'clientId': 'input-browser'}]
    original = app._app_bridge

    async def inspect(operation, args, session_id, **options):
        assert args == {'action': 'observation.preview', 'args': {}}
        assert options['input_origin'] == {'clientId': 'input-browser', 'status': 'client'}
        return await original(operation, args, session_id, **options)

    async def dispatch(action, args, **options):
        assert action == 'observation.preview'
        assert options['origin'] == 'agent' and options['caller_session_id'] == root['id']
        assert app.observations.input_bindings.get() == expected
        assert BINDING.get()['_inputBindings'] == expected
        assert PRINCIPAL.get() == 'native-root'
        return {'accepted': True, 'result': {'preview': True}}

    monkeypatch.setattr(app, '_app_bridge', inspect)
    monkeypatch.setattr(app, 'dispatch', AsyncMock(side_effect=dispatch))
    previous = app.observations.input_bindings.get()
    result = await worker.app_access_bridge(worker.session.coordinator)(
        'dispatch', {'action': 'observation.preview', 'args': {}})
    assert result['result']['preview'] is True
    assert app.observations.input_bindings.get() == previous
    assert PRINCIPAL.get() is None and BINDING.get() is None


async def test_context_operations_retain_worker_specific_bindings(worker_bridge, monkeypatch):
    app, root, worker, envelopes = worker_bridge
    seen = []

    def manifest(session_id, bindings):
        assert session_id == root['id']
        seen.append(deepcopy(bindings))
        return {'surfaces': []}

    def read(session_id, args, bindings):
        assert session_id == root['id']
        assert args['surfaceId'] == 'surface-1'
        assert args['_contextInputs'] == ['input-1']
        assert args['_contextBindings'] == bindings
        assert not TRANSPORT_FIELDS.intersection(args)
        return {'surfaceId': args['surfaceId']}

    monkeypatch.setattr(app.surface_context, 'manifest', manifest)
    monkeypatch.setattr(app.surface_context, 'read', read)
    bridge = worker.app_access_bridge(worker.session.coordinator)
    result = await bridge('context.manifest', {'_contextBindings': [{'clientId': 'forged'}]})
    assert result['inputIds'] == ['input-1']
    assert seen[-1] == [worker.context_bindings['input-1']]
    assert (await bridge('context.read', {'surfaceId': 'surface-1'}))['surfaceId'] == 'surface-1'
    # Delegates freeze assignment and narrow surface targets; do not remove
    # these context-specific fields along with the common transport envelope.
    child = worker.app_access_bridge(SimpleNamespace(session_id='native-child'))
    worker.context_inputs = ['later-input']
    worker.context_bindings['later-input'] = {'clientId': 'old-browser', 'targets': []}
    await child('context.manifest', {})
    assert seen[-1] == [{'clientId': 'input-browser', 'targets': []}]


async def test_child_transport_cannot_borrow_root_principal_or_generation(worker_bridge, monkeypatch):
    app, root, worker, envelopes = worker_bridge
    child = worker.app_access_bridge(SimpleNamespace(session_id='native-child'))
    worker.context_inputs = ['later-input']
    worker.context_bindings.clear()

    async def inspect(operation, args, session_id, **options):
        assert not TRANSPORT_FIELDS.intersection(args)
        assert PRINCIPAL.get() == 'native-child'
        assert '_generationId' not in BINDING.get()
        assert BINDING.get()['_inputBindings'] == [{'inputId': 'input-1', 'clientId': 'input-browser'}]
        assert app.clients.current.get() == 'input-browser'
        app.collaboration.source({}, 'agent', session_id)

    monkeypatch.setattr(app, '_app_bridge', inspect)
    with pytest.raises(AppError, match="child cannot borrow"):
        await child('list_actions', {'_generationId': 'generation-root', '_runtimeSessionId': 'native-root'})
    assert PRINCIPAL.get() is None and BINDING.get() is None
    assert app.clients.current.get() is None