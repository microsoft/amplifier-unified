"""A helper process does not change a source that it imports a module from.

Seen on a new Windows installation (0.106.3): "Save and check connection" for
an "OpenAI Compatible API" connection passed, and then no chat with that
connection started ("Configured modules could not be prepared"), and the next
component update failed. The connection check runs in a helper process
(``provider_probe.py``) with the host's environment. On Windows that
environment names the shared source store. The helper prepared the module from
its source, imported it from the store object, and Python wrote
``__pycache__`` into the object. From then on Foundation refused the object:
"Shared source contents changed".

Windows shows it and Linux and macOS do not, for one reason: Foundation takes
the write permission from every folder of an object, and a read-only attribute
on a Windows folder does not stop a new entry in it.

The rule is ``amplifier_web.source_imports.prepare``: a helper that prepares a
module from a source writes no bytecode from that call on, and a Python
process that it starts writes none. Three helpers
prepare a module from a source with the host's environment: the connection
check, the readiness check of a transfer, and the module validation.

The two cases of the whole way use the real helper file, the product's own
start of it, Foundation's real code, and a Git repository on this computer.
They send nothing to a network. No case here skips on any platform.
"""
from __future__ import annotations

import asyncio
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from types import SimpleNamespace

import pytest

from amplifier_web.host.config import HostConfig, merge
from amplifier_web.setup import SetupManager

HELPER = Path(__file__).resolve().parents[1] / 'amplifier_web' / 'provider_probe.py'
MODULE, PACKAGE, CONNECTION = 'provider-store-case', 'amplifier_module_provider_store_case', 'store-case-one'

# A provider module with two files, as a real one has. It says where Python
# loaded it from, whether that process writes bytecode, and whether a Python
# process that it starts writes bytecode.
FILES = {
    # The marker of a Python module for Foundation. There is no pyproject.toml,
    # so the helper installs nothing into the environment of these tests.
    'setup.cfg': '[metadata]\nname = amplifier-module-provider-store-case\n',
    '.gitattributes': '* -text\n',
    PACKAGE + '/_listing.py': (
        'def models(loaded_from, writes_bytecode, child_writes_bytecode):\n'
        "    return [{'id': 'store-case-model', 'loadedFrom': loaded_from, 'writesBytecode': writes_bytecode,\n"
        "             'childWritesBytecode': child_writes_bytecode}]\n"),
    PACKAGE + '/__init__.py': (
        'from pathlib import Path\n'
        'import subprocess\n'
        'import sys\n'
        'from ._listing import models\n\n'
        '# A Python process that this process starts, and that imports this package from the same folder.\n'
        "CHILD = ('import sys; sys.path.insert(0, sys.argv[1]); '\n"
        "         'import amplifier_module_provider_store_case._listing; print(not sys.dont_write_bytecode)')\n\n\n"
        'class StoreCaseProvider:\n'
        '    def __init__(self, *, config=None):\n'
        '        self.config = config or {}\n\n'
        '    def get_info(self):\n'
        "        return {'id': 'store-case', 'display_name': 'Store case', 'config_fields': []}\n\n"
        '    def get_model_list_info(self):\n'
        "        return {'version': 1, 'source': 'static'}\n\n"
        '    async def list_models(self):\n'
        '        child = subprocess.run([sys.executable, "-c", CHILD, str(Path(__file__).parent.parent)],\n'
        '                               capture_output=True, text=True, check=True)\n'
        '        return models(__file__, not sys.dont_write_bytecode, child.stdout.strip() == "True")\n'),
}



def repository(folder):
    """A Git repository on this computer with the module at the branch main."""
    for name, text in FILES.items():
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8', newline='\n')

    def git(*arguments):
        subprocess.run(['git', '-c', 'user.name=case', '-c', 'user.email=case@example.invalid',
                        '-c', 'commit.gpgsign=false', '-c', 'core.autocrlf=false', *arguments],
                       cwd=folder, check=True, capture_output=True)
    git('init', '--quiet', '--initial-branch=main')
    git('add', '--all')
    git('commit', '--quiet', '--message', 'the module')
    return 'git+' + folder.as_uri() + '@main'


def bytecode(folder):
    return sorted(path.relative_to(folder).as_posix() for path in folder.rglob('*')
                  if path.name == '__pycache__' or path.suffix == '.pyc')


@pytest.fixture
def connection(tmp_path, monkeypatch):
    """A saved connection whose module has a source, and the product's own start of the real helper."""
    source = repository(tmp_path / 'repository')
    home, workspace = tmp_path / 'app', tmp_path / 'workspace'
    workspace.mkdir()
    manager = SetupManager(home, probe_command=[sys.executable, str(HELPER)])

    def config(folder):
        settings = {}
        for scope in ('global', 'project', 'local'):
            settings = merge(settings, manager.store.read(folder, scope))
        return HostConfig(home=manager.home, workspace=Path(folder), settings=settings,
                          registry_home=manager.home / 'foundation')
    monkeypatch.setattr(manager, 'config', config)
    saved = {'id': CONNECTION, 'module': MODULE, 'source': source, 'config': {}}
    manager.store.update(workspace, 'global', lambda settings: settings.update(config={'providers': [saved]}))

    async def check(store):
        """The person selects "Save and check connection" on a host with this store name, or with none."""
        # The helper gets the host's environment. A host has no bytecode
        # setting; a job or a shell that has one must not hide that.
        monkeypatch.delenv('PYTHONDONTWRITEBYTECODE', raising=False)
        if store is None:
            monkeypatch.delenv('AMPLIFIER_SOURCE_STORE', raising=False)
        else:
            monkeypatch.setenv('AMPLIFIER_SOURCE_STORE', str(store))
        return (await manager.probe('providers.models', {'id': CONNECTION}, str(workspace)))['models'][0]
    return SimpleNamespace(check=check, source=source, cache=home / 'foundation' / 'cache')


async def test_the_connection_check_leaves_the_store_object_as_foundation_published_it(
        connection, tmp_path, monkeypatch):
    """A host whose environment names the store: a Windows installation."""
    from amplifier_foundation import Bundle
    from amplifier_foundation.sources.shared import SharedSourceStore
    from amplifier_web.provider_isolation import ProviderIsolation

    store = tmp_path / 's'
    model = await connection.check(store)
    binding, = (connection.cache / '.source-bindings').glob('*.json')
    record = json.loads(binding.read_text())
    shared = SharedSourceStore(store)
    published = shared.checkout(record['git_url'], record['commit'])
    try:
        await asyncio.to_thread(shared.verify, record['git_url'], record['commit'])
        verified = 'accepted'
    except ValueError as error:
        verified = f'refused: {error}'

    # The next preparation of the same module from the same source, with the
    # worker's rule for a provider that fails. A conversation worker does this
    # in its own process; this case does it here.
    monkeypatch.setattr(sys, 'path', list(sys.path))
    isolation = ProviderIsolation()
    provider = {'module': MODULE, 'source': connection.source, 'config': {}}
    await Bundle.from_dict({'bundle': {'name': 'worker'}, 'providers': [provider]}).prepare(
        strict=True, install_deps=False, cache_dir=connection.cache,
        provider_failure_policy=isolation.preparation_failed)
    failure = isolation.failure(provider)

    assert {
        'the helper loaded the module from the store object': Path(model['loadedFrom']).parent.parent == published,
        'the helper writes bytecode': model['writesBytecode'],
        'a Python process that the helper starts writes bytecode': model['childWritesBytecode'],
        'bytecode in the object': bytecode(published),
        'Foundation, asked for the object again': verified,
        "the worker's record for the connection": failure and {
            name: failure.get(name) for name in ('reason_code', 'diagnostic')},
    } == {
        'the helper loaded the module from the store object': True,
        'the helper writes bytecode': False,
        'a Python process that the helper starts writes bytecode': False,
        'bytecode in the object': [],
        'Foundation, asked for the object again': 'accepted',
        "the worker's record for the connection": None,
    }


async def test_on_a_host_without_a_store_the_connection_check_leaves_the_checkout_clean(connection):
    """Then the helper imports from a checkout in Foundation's cache, on every platform."""
    from amplifier_foundation.sources.shared import clean_checkout
    model = await connection.check(None)
    checkout = Path(model['loadedFrom']).parent.parent
    assert {
        'the helper loaded the module from a checkout in the cache':
            checkout.resolve().parent == connection.cache.resolve(),
        'the helper writes bytecode': model['writesBytecode'],
        'a Python process that the helper starts writes bytecode': model['childWritesBytecode'],
        'bytecode in the checkout': bytecode(checkout),
        'Foundation finds the checkout clean': clean_checkout(checkout),
    } == {
        'the helper loaded the module from a checkout in the cache': True,
        'the helper writes bytecode': False,
        'a Python process that the helper starts writes bytecode': False,
        'bytecode in the checkout': [],
        'Foundation finds the checkout clean': True,
    }


@pytest.fixture
def preparation(monkeypatch):
    """Stands in for Foundation's bundle. It records whether the process writes bytecode when preparation starts."""
    import amplifier_foundation
    seen = []

    class Source:
        @staticmethod
        def from_dict(value):
            return Source()

        async def prepare(self, **options):
            seen.append({'writes bytecode': not sys.dont_write_bytecode,
                         'its Python children write bytecode': os.environ.get('PYTHONDONTWRITEBYTECODE') != '1'})
            raise RuntimeError('The case ends here: what the helper does with the module is not its subject.')

    saved = dict(os.environ)   # Each helper writes names for its preparation.
    monkeypatch.setattr(amplifier_foundation, 'Bundle', Source)
    # pytest itself can run with -B, and a job or a shell can have the
    # setting. The helper must turn bytecode writing off itself.
    monkeypatch.setattr(sys, 'dont_write_bytecode', False)
    os.environ.pop('PYTHONDONTWRITEBYTECODE', None)
    yield seen
    os.environ.clear()
    os.environ.update(saved)


async def connection_check(tmp_path, monkeypatch):
    from amplifier_web import provider_probe
    with pytest.raises(provider_probe.ProbeFailure, match='preparation'):
        await provider_probe.query({'module': MODULE, 'action': 'providers.models', 'source': 'a-source',
                                    'registryHome': str(tmp_path / 'registry'), 'config': {}})


async def transfer_readiness_check(tmp_path, monkeypatch):
    from amplifier_web import portability_probe
    from amplifier_web.portability_policy import readiness_policy
    # The transfer fence is not the subject. tests/test_portability_probe.py runs it.
    monkeypatch.setattr(portability_probe, '_runtime_transfer_fence', lambda: None)
    result = await portability_probe.probe({
        'module': MODULE, 'config': {}, 'model': 'a-model', 'source': 'a-source',
        'registryHome': str(tmp_path / 'registry'),
        'readinessPolicy': readiness_policy(MODULE, 'store-case', 'a-model', 'high')})
    assert result['code'] == 'probe_failed'


async def module_validation(tmp_path, monkeypatch):
    from amplifier_web import module_validate
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path / 'app'))
    monkeypatch.setattr(sys, 'stdin', io.StringIO(json.dumps(
        {'id': CONNECTION, 'module': MODULE, 'section': 'providers', 'source': 'a-source'}) + '\n'))
    await module_validate.main()


@pytest.mark.parametrize('helper', [connection_check, transfer_readiness_check, module_validation],
                         ids=lambda helper: helper.__name__)
async def test_each_helper_that_prepares_a_module_from_a_source_writes_no_bytecode_from_then_on(
        helper, preparation, tmp_path, monkeypatch, capsys):
    await helper(tmp_path, monkeypatch)
    assert preparation == [{'writes bytecode': False, 'its Python children write bytecode': False}]
    # Also for the imports and the child processes that follow the preparation.
    assert sys.dont_write_bytecode is True and os.environ['PYTHONDONTWRITEBYTECODE'] == '1'


def test_a_helper_starts_the_preparation_of_a_bundle_only_through_the_rule():
    """A reminder for the next helper, not a proof.

    It finds the call by the two names that this code gives a Foundation
    bundle, and the call on the result of another call
    (``Bundle.from_dict(...).prepare(``). Another name passes unseen.
    ``host/components.py`` prepares for conversation workers and update
    stages; their environment turns bytecode writing off
    (``update_sources.store_environment``).
    """
    package = HELPER.parent
    callers = sorted(
        path.relative_to(package).as_posix() for path in package.rglob('*.py')
        if 'static' not in path.relative_to(package).parts
        and re.search(r'(?:\b(?:bundle|projected)|\))\s*\.prepare\(', path.read_text(encoding='utf-8')))
    assert callers == ['host/components.py', 'source_imports.py'], (
        'A helper that prepares a module from a source calls amplifier_web.source_imports.prepare, '
        'not the bundle. Else it writes __pycache__ into the source, and Foundation then refuses a store object.')
