"""A provider module's own state files are in the shared Amplifier home.

The Anthropic module builds the default paths of two small files from the
person's home folder and does not read ``AMPLIFIER_HOME``. A Windows
installation from the setup file has its shared Amplifier home in the
installation folder, so a chat wrote ``%USERPROFILE%\\.amplifier\\
rate-limit-state.json`` outside it (seen on a real PC). The host now gives the
module paths in the shared home (``amplifier_web.provider_state_files``).

The cases with the real module use the locked test dependencies and an HTTP
server on this computer. They send nothing to a model. The CI jobs set
``AMPLIFIER_REQUIRE_PROVIDER_STATE_FILES``: then a missing module fails; it
never skips.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
import importlib
import importlib.util
import inspect
import json
import os
from pathlib import Path
import re
import socket
import sys
from types import SimpleNamespace

import pytest

from amplifier_web import provider_state_files
from amplifier_web.host.initialization import initialization_hooks
from amplifier_web.provider_state_files import STATE_FILES, state_file_defaults, uses_state_files, with_state_files
from test_host_session_resume import mounted_host  # noqa: F401  (a fixture of credential_host)
from test_provider_environment import binding_plan, credential_host  # noqa: F401

REQUIRED = os.environ.get('AMPLIFIER_REQUIRE_PROVIDER_STATE_FILES') == '1'
ANTHROPIC = 'provider-anthropic'
RATE, FALLBACK = 'rate-limit-state.json', 'anthropic-fallback-state.json'


def real(name):
    """A module of the locked test dependencies."""
    try:
        return importlib.import_module(name)
    except ImportError as error:
        reason = f'{name} is not installed: {error}'
        if REQUIRED:
            pytest.fail(reason)
        pytest.skip(reason)


@pytest.fixture
def folders(tmp_path, monkeypatch):
    """A person's profile and, beside it, an installation with its shared home."""
    profile, shared = tmp_path / 'profile', tmp_path / 'Amplifier' / 'shared'
    profile.mkdir()
    shared.mkdir(parents=True)
    for name in ('HOME', 'USERPROFILE'):
        monkeypatch.setenv(name, str(profile))
    for name in ('HOMEDRIVE', 'HOMEPATH'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('AMPLIFIER_HOME', str(shared))
    monkeypatch.setenv('NO_PROXY', '127.0.0.1')
    # Nothing in this file may reach the real home folder.
    assert Path(os.path.expanduser('~')) == profile
    return SimpleNamespace(profile=profile, shared=shared.resolve(), declared=str(shared))


def below(folder):
    return sorted(path.relative_to(folder).as_posix() for path in folder.rglob('*'))


def paths(shared):
    return {'rate_limit_state_path': str(shared / RATE), 'fallback_state_path': str(shared / FALLBACK)}


# The rule.


def test_the_paths_are_in_the_shared_home_and_only_for_a_module_that_has_such_files(folders):
    assert state_file_defaults(ANTHROPIC) == paths(folders.shared)
    assert with_state_files(ANTHROPIC, {'api_key': 'k'}) == {'api_key': 'k', **paths(folders.shared)}
    assert with_state_files(ANTHROPIC, None) == paths(folders.shared)
    other = {'api_key': 'k'}
    assert state_file_defaults('provider-openai') == {}
    assert with_state_files('provider-openai', other) is other and other == {'api_key': 'k'}
    assert with_state_files('provider-openai', None) is None
    assert uses_state_files([{'module': 'provider-openai'}, {'module': ANTHROPIC}])
    assert not uses_state_files([{'module': 'provider-openai'}]) and not uses_state_files(None)


@pytest.mark.parametrize('configured', ['', 'D:\\elsewhere\\state.json', None])
def test_a_configured_value_stays_also_an_empty_one(folders, configured):
    given = {'rate_limit_state_path': configured}
    result = with_state_files(ANTHROPIC, given)
    assert result == {'rate_limit_state_path': configured, 'fallback_state_path': str(folders.shared / FALLBACK)}
    assert given == {'rate_limit_state_path': configured}  # The given configuration is not changed.


@pytest.mark.parametrize('declared', ['unset', 'named'])
def test_where_the_shared_home_is_the_modules_own_folder_the_host_sets_nothing(folders, monkeypatch, declared):
    if declared == 'unset':
        monkeypatch.delenv('AMPLIFIER_HOME')
    else:
        monkeypatch.setenv('AMPLIFIER_HOME', str(folders.profile / '.amplifier'))
    config = {'api_key': 'k'}
    assert state_file_defaults(ANTHROPIC) == {}
    assert with_state_files(ANTHROPIC, config) is config and config == {'api_key': 'k'}


# The host gives the paths to Core's loader. The plan does not hold them.


class Loader:
    def __init__(self):
        self.calls = []

    async def load(self, module_id, config=None, **options):
        self.calls.append((module_id, config, options))
        return 'mount'


def hooks(providers):
    return initialization_hooks(isolation=None, decision_accounts=SimpleNamespace(records=[]), routing=None,
                                retain=lambda session: None, providers=providers)


async def test_the_session_step_wraps_the_loader_that_it_finds(folders):
    loader = Loader()
    session = SimpleNamespace(coordinator=SimpleNamespace(loader=loader))
    await hooks([{'module': ANTHROPIC}])['before_initialize'](session)
    assert session.coordinator.loader is loader
    plain = {'api_key': 'k'}
    assert await loader.load(ANTHROPIC, plain, source_hint='s', coordinator='c') == 'mount'
    assert await loader.load('tool-bash', plain) == 'mount'
    assert loader.calls == [(ANTHROPIC, {'api_key': 'k', **paths(folders.shared)}, {'source_hint': 's', 'coordinator': 'c'}),
                            ('tool-bash', plain, {})]
    assert loader.calls[1][1] is plain and plain == {'api_key': 'k'}


def test_a_session_without_such_a_provider_gets_no_step():
    assert hooks([{'module': 'provider-openai'}]) == {}
    assert hooks(()) == {}
    assert set(hooks([{'module': ANTHROPIC}])) == {'before_initialize'}


async def test_media_observes_declared_options_before_provider_receives_local_paths(folders):
    loader = Loader()
    session = SimpleNamespace(coordinator=SimpleNamespace(loader=loader))
    observed = []

    def bind_media(constructing):
        downstream = constructing.coordinator.loader

        async def observe(module_id, config, **options):
            observed.append((module_id, dict(config)))
            return await downstream.load(module_id, config, **options)

        constructing.coordinator.loader = SimpleNamespace(load=observe)

    declared = {'priority': 2, 'fallback_state_path': ''}
    providers = [{'module': ANTHROPIC, 'config': declared}]
    step = initialization_hooks(isolation=None, decision_accounts=SimpleNamespace(records=[]),
        routing=None, retain=lambda constructing: None, providers=providers, media=bind_media)
    await step['before_initialize'](session)
    await session.coordinator.loader.load(ANTHROPIC, declared, source_hint='selected')

    assert observed == [(ANTHROPIC, {'priority': 2, 'fallback_state_path': ''})]
    assert loader.calls == [(ANTHROPIC, {**paths(folders.shared), **declared}, {'source_hint': 'selected'})]


async def test_a_root_chat_gets_the_step_and_its_plan_holds_no_path(credential_host, monkeypatch):  # noqa: F811
    from amplifier_web.host import session as host
    from amplifier_web.session_files import amplifier_home
    h = credential_host
    loader = Loader()
    h.session.coordinator.loader = loader
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'synthetic-unrelated-ambient')
    h.load(binding_plan())
    await host.prepare_manager(h.config.workspace, runtime=h.runtime, qualification_readonly=True)
    assert h.session.coordinator.loader is loader
    await loader.load(ANTHROPIC, {'api_key': 'k'})
    assert loader.calls == [(ANTHROPIC, {'api_key': 'k', **paths(amplifier_home())}, {})]
    assert 'state_path' not in json.dumps(h.prepared.mount_plan) + json.dumps(h.session.coordinator.config)


SPY = '''
from amplifier_core import ProviderInfo
__amplifier_module_type__ = "provider"
SEEN = []
class Spy:
    priority = 1
    default_model = "fixture-model"
    def __init__(self, config):
        self.name, self.config = config["family"], config
    def get_info(self):
        return ProviderInfo(id=self.name, display_name=self.name, defaults={"model": self.default_model})
    async def list_models(self):
        return []
    def get_config_schema(self):
        return {"fields": []}
    async def complete(self, request, **kwargs):
        raise AssertionError("No model call is permitted")
    def parse_tool_calls(self, response):
        return []
async def mount(coordinator, config=None):
    SEEN.append((coordinator.session_id, config))
    spy = Spy(config)
    await coordinator.mount("providers", spy, name=spy.name)
'''


def prepared_bundle(tmp_path, providers, modules):
    """Foundation's prepared bundle with the real loop and context modules."""
    from amplifier_foundation.bundle import Bundle, BundleModuleResolver, PreparedBundle
    orchestrator = real('amplifier_module_loop_live.orchestrator')
    real('amplifier_module_context_simple')

    class ObserveOnly(orchestrator.BundleLiveOrchestrator):
        async def execute(self, prompt, context, providers, tools, hooks, coordinator):
            return 'no model call'

    @dataclass
    class Prepared(PreparedBundle):
        async def create_session(self, **options):
            session = await super().create_session(**options)
            await session.coordinator.mount('orchestrator', ObserveOnly({}))
            return session

    plan = {'session': {'orchestrator': {'module': 'loop-live'}, 'context': {'module': 'context-simple'}},
            'providers': providers}
    locations = {name: Path(importlib.util.find_spec('amplifier_module_' + name.replace('-', '_')).origin).parent
                 for name in ('loop-live', 'context-simple')}
    locations.update(modules)
    bundle = Bundle.from_dict({'bundle': {'name': 'state-files'}, **plan}, base_path=tmp_path)
    plan = bundle.to_mount_plan()
    return Prepared(copy.deepcopy(plan), BundleModuleResolver(locations), bundle), plan


@pytest.fixture
def spies(tmp_path):
    package = tmp_path / 'amplifier_module_provider_state_file_spy'
    package.mkdir()
    (package / '__init__.py').write_text(SPY, encoding='utf-8')
    rows = [{'module': ANTHROPIC, 'config': {'family': 'anthropic'}},
            {'module': 'provider-openai', 'config': {'family': 'openai'}}]
    prepared, plan = prepared_bundle(tmp_path, rows, {ANTHROPIC: package, 'provider-openai': package})
    yield SimpleNamespace(prepared=prepared, plan=plan, seen=lambda: sys.modules[package.name].SEEN)
    sys.modules.pop(package.name, None)


@pytest.mark.parametrize('fixed', [False, True])
async def test_core_loads_the_provider_of_a_root_chat_with_the_paths(folders, spies, fixed):
    """Core's own initialization uses the loader that the host supplies."""
    step = hooks(spies.plan['providers'] if fixed else ())
    session = await spies.prepared.create_session(session_id='root', **step)
    try:
        seen = {config['family']: config for _, config in spies.seen()}
        assert seen['openai'] == {'family': 'openai'}
        assert seen['anthropic'] == {'family': 'anthropic', **(paths(folders.shared) if fixed else {})}
        # The mounted plan is the declared one. It is what the app saves, hashes, compares and exports.
        assert session.coordinator.config == spies.plan == spies.prepared.mount_plan
    finally:
        await session.cleanup()


async def test_a_child_chat_gets_the_paths_and_its_saved_plan_holds_none(folders, spies, tmp_path):
    from amplifier_module_loop_live.runtime import Runtime
    from amplifier_web.host.children import install_children
    from amplifier_web.host.storage import SessionStore
    parent = await spies.prepared.create_session(session_id='root', **hooks(spies.plan['providers']))
    try:
        parent.coordinator.register_capability('session.working_dir', str(tmp_path))
        store = SessionStore(tmp_path / 'children')
        registry = await install_children(parent, spies.prepared, Runtime('root'), store, None)
        result = await registry.spawn('self', 'say nothing', parent, sub_session_id='child')
        assert result['output'] == 'no model call'
        child = {config['family']: config for identity, config in spies.seen() if identity == 'child'}
        assert child['anthropic'] == {'family': 'anthropic', **paths(folders.shared)}
        assert child['openai'] == {'family': 'openai'}
        assert parent.coordinator.config == spies.plan
        saved = (tmp_path / 'children' / 'child' / 'metadata.json').read_text(encoding='utf-8')
        assert '"mount_plan"' in saved and 'state_path' not in saved and str(folders.shared) not in saved
    finally:
        await parent.cleanup()


# The settings page and the transfer check construct a provider without a session.


def module_source(registry, prepared):
    """Stands in for Foundation's bundle of one module source. It records the home that preparation sees."""
    class Source:
        @staticmethod
        def from_dict(value):
            return Source()

        async def prepare(self, strict):
            # The registry of this process, as a probe sets it for a module source.
            assert os.environ['AMPLIFIER_HOME'] == str(registry)
            prepared.append(os.environ['AMPLIFIER_HOME'])

    return Source


async def test_a_connection_test_gets_the_paths_that_it_read_before_the_registry_home(folders, monkeypatch, tmp_path):
    import amplifier_foundation
    from amplifier_web import provider_probe
    constructed, prepared = [], []

    class Provider:
        def get_info(self):
            return {}

        def get_image_generation_info(self):
            return {'schemaVersion': 1, 'configKey': 'image_generation'}

        async def list_image_models(self):
            return []

    async def schema(*arguments, **options):
        return {'fields': []}

    async def close(*arguments):
        pass

    def construct(cls, config):
        constructed.append(config)
        return Provider()

    monkeypatch.setattr(amplifier_foundation, 'Bundle', module_source(tmp_path / 'registry', prepared))
    monkeypatch.setattr(provider_probe, 'provider_class', lambda module: Provider)
    monkeypatch.setattr(provider_probe, 'construct_schema_provider', lambda *arguments: Provider())
    monkeypatch.setattr(provider_probe, 'construct_provider', construct)
    monkeypatch.setattr(provider_probe, 'config_schema', schema)
    monkeypatch.setattr(provider_probe, 'close_provider', close)
    request = {'module': ANTHROPIC, 'action': 'providers.imageModels', 'source': 'fixture-source',
               'registryHome': str(tmp_path / 'registry'), 'config': {'fallback_state_path': ''}}
    await provider_probe.query(request)
    assert prepared == [str(tmp_path / 'registry')]   # The home did change before the provider was constructed.
    assert constructed == [{'rate_limit_state_path': str(folders.shared / RATE), 'fallback_state_path': ''}]
    await provider_probe.query({'module': 'provider-openai', 'action': 'providers.imageModels', 'config': {'a': 1}})
    assert constructed[1] == {'a': 1}


@pytest.mark.parametrize('source', [None, 'fixture-source'], ids=['installed module', 'module source'])
async def test_the_readiness_check_of_a_transfer_asks_the_model_with_the_paths(folders, monkeypatch, tmp_path, source):
    import amplifier_foundation
    from amplifier_web import portability_probe
    from amplifier_web.portability_policy import readiness_policy
    constructed, asked, prepared = [], [], []

    class Provider:
        def __init__(self, *, api_key=None, config=None):
            self.config = config
            constructed.append(dict(config))   # A copy: what the provider has at this moment.

        def get_info(self):
            return {'capabilities': ['completion:single_attempt:v1', 'completion:single_attempt:v2'], 'config_fields': []}

        async def complete(self, request, **options):
            asked.append(dict(self.config))   # The model call. Its answer is not the subject here.
            raise RuntimeError('no answer')

        async def close(self):
            pass

    # The transfer fence is not the subject. tests/test_portability_probe.py runs it.
    monkeypatch.setattr(portability_probe, '_runtime_transfer_fence', lambda: None)
    monkeypatch.setattr(portability_probe, 'provider_class', lambda module: Provider)
    monkeypatch.setattr(amplifier_foundation, 'Bundle', module_source(tmp_path / 'registry', prepared))
    request = {'module': ANTHROPIC, 'config': {'api_key': 'k', 'fallback_state_path': ''}, 'model': 'selected-model',
               'readinessPolicy': readiness_policy(ANTHROPIC, 'anthropic', 'selected-model', 'high')}
    if source:
        request.update(source=source, registryHome=str(tmp_path / 'registry'))
    result = await portability_probe.probe(request)
    assert result['code'] == 'probe_failed'   # The stand-in gives no answer.
    # With a module source the home changes for the preparation, and it is the shared home again afterwards.
    assert prepared == ([str(tmp_path / 'registry')] if source else [])
    assert os.environ['AMPLIFIER_HOME'] == folders.declared
    # The first instance reads the schema only. The second gets the model call. A configured value stays.
    expected = {'api_key': 'k', 'fallback_state_path': '', 'rate_limit_state_path': str(folders.shared / RATE)}
    assert constructed == [{}, expected] and asked == [expected]


# The real module: the cause, and the fix.


async def anthropic_api(overloaded=()):
    """An HTTP server on this computer that answers as the Anthropic API does."""
    from aiohttp import web
    calls = []

    async def model(request):
        calls.append(('GET', request.match_info['identity']))
        return web.json_response({'id': request.match_info['identity'], 'type': 'model', 'display_name': 'Fixture',
                                  'created_at': '2026-11-09T00:00:00Z'})

    async def messages(request):
        body = await request.json()
        calls.append(('POST', body['model']))
        if body['model'] in overloaded:
            return web.json_response({'type': 'error', 'error': {'type': 'overloaded_error', 'message': 'Overloaded'}},
                                     status=529)
        sent = sum(1 for method, _ in calls if method == 'POST')
        return web.json_response(
            {'id': 'msg_1', 'type': 'message', 'role': 'assistant', 'model': body['model'],
             'content': [{'type': 'text', 'text': 'OK'}], 'stop_reason': 'end_turn', 'stop_sequence': None,
             'usage': {'input_tokens': 1, 'output_tokens': 1}},
            headers={'anthropic-ratelimit-requests-limit': '50',
                     'anthropic-ratelimit-requests-remaining': str(50 - sent),
                     'anthropic-ratelimit-requests-reset': '2026-11-17T12:00:00Z'})

    application = web.Application()
    application.router.add_get('/v1/models/{identity}', model)
    application.router.add_post('/v1/messages', messages)
    runner = web.AppRunner(application)
    await runner.setup()
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    await web.SockSite(runner, listener).start()
    return runner, f'http://127.0.0.1:{listener.getsockname()[1]}', calls


CASES = {
    # The file that the real PC had: written after each answer that carries rate-limit headers.
    'rate limit': ({'default_model': 'claude-sonnet-5'}, (), RATE),
    # The second file exists only with this option, after an overloaded model. "" turns the first file off.
    'fallback': ({'default_model': 'claude-opus-4-6', 'rate_limit_state_path': '', 'fallback_on_overload': True,
                  'persist_fallback_state': True, 'fallback_retry_count': 1,
                  'fallback_models': {'opus': 'claude-sonnet-4-6'}, 'min_retry_delay': 0.0, 'max_retry_delay': 0.0},
                 ('claude-opus-4-6',), FALLBACK),
}


@pytest.mark.parametrize('fixed', [False, True], ids=['as before', 'with the host paths'])
@pytest.mark.parametrize('case', list(CASES))
async def test_the_real_module_writes_its_file_in_the_shared_home_and_nothing_in_the_profile(
        folders, tmp_path, case, fixed):
    module = real('amplifier_module_provider_anthropic')
    from amplifier_core import ChatRequest, Message
    settings, overloaded, name = CASES[case]
    # The module keeps a fallback window for the whole process. Each case starts and ends without one.
    module._clear_fallback_windows()
    runner, address, calls = await anthropic_api(overloaded)
    row = {'module': ANTHROPIC, 'config': {'api_key': 'synthetic-not-live', 'base_url': address, 'use_streaming': False,
                                           'max_retries': 0, 'enable_prompt_caching': False, **settings}}
    prepared, plan = prepared_bundle(tmp_path, [row], {ANTHROPIC: Path(module.__file__).parent})
    session = await prepared.create_session(session_id='root', **hooks(plan['providers'] if fixed else ()))
    try:
        provider = session.coordinator.get('providers')['anthropic']
        for _ in range(2):
            await provider.complete(ChatRequest(messages=[Message(role='user', content='Say OK.')]))
        assert [method for method, _ in calls].count('POST') >= 2
        assert all(model in {call[1] for call in calls} for model in overloaded)   # The overloaded model was asked.
        if fixed:
            assert below(folders.profile) == []            # No folder and no file in the person's profile.
            assert below(folders.shared) == [name]         # The file, and no temporary file beside it.
        else:
            assert below(folders.profile) == ['.amplifier', '.amplifier/' + name]   # What the real PC had.
            assert below(folders.shared) == []
        assert session.coordinator.config == plan and str(folders.shared) not in json.dumps(session.coordinator.config)
    finally:
        module._clear_fallback_windows()
        await session.cleanup()
        await runner.cleanup()


def test_the_names_are_the_ones_the_module_uses():
    """A third file, or another name, in a later module version fails here."""
    text = inspect.getsource(real('amplifier_module_provider_anthropic'))
    joined = re.sub(r'\s+', '', text)
    for key, name in STATE_FILES[ANTHROPIC].items():
        assert f'os.path.expanduser("~"),".amplifier","{name}"' in joined
        assert f'self.config.get("{key}",' in joined
    assert text.count('os.path.expanduser("~")') == len(STATE_FILES[ANTHROPIC]) == 2
    for other in ('Path.home(', 'USERPROFILE', 'APPDATA', 'XDG_', 'tempfile.', 'gettempdir'):
        assert other not in text, other   # No other way to a folder outside the configured paths.
    assert text.count('tmp_path = path + ".tmp"') == 2   # Each file is written beside itself, then renamed.


def test_the_files_fit_the_windows_path_budget():
    import test_windows_path_budget as budget
    names = [name + suffix for name in STATE_FILES[ANTHROPIC].values() for suffix in ('', '.tmp')]
    # An installation: AMPLIFIER_HOME is <installation folder>\shared.
    lengths = {name: budget.length(f'shared\\{name}') for name in names}
    assert max(lengths.values()) == lengths[FALLBACK + '.tmp'] == budget.MAX_ROOT + 41 <= budget.FILE_LIMIT
    # An update stage checks its candidate with AMPLIFIER_HOME on <stage>\shared-config.
    stage = max(budget.length(f'{budget.STAGE}\\shared-config\\{name}') for name in names)
    assert stage == budget.MAX_ROOT + 102 <= budget.FILE_LIMIT
    consumer = (budget.ROOT / 'amplifier_web/windows_consumer.py').read_text(encoding='utf-8')
    assert '"AMPLIFIER_HOME": "shared"' in consumer
    assert provider_state_files.STATE_FILES == {ANTHROPIC: {'rate_limit_state_path': RATE, 'fallback_state_path': FALLBACK}}
