"""A helper process does not change an object of the shared source store.

Seen on a new Windows installation (0.106.3): "Save and check connection" for
an "OpenAI Compatible API" connection passed, and then no chat with that
connection started ("Configured modules could not be prepared"), and the next
component update failed. The connection check runs in a helper process
(``provider_probe.py``) with the host's environment. On Windows that
environment names the shared source store, and it did not turn bytecode
writing off. The helper prepared the module from its source, imported it from
the store object, and Python wrote ``__pycache__`` into the object. From then
on Foundation refused the object: "Shared source contents changed".

Windows shows it and Linux and macOS do not, for one reason: Foundation takes
the write permission from every folder of an object, and a read-only attribute
on a Windows folder does not stop a new entry in it.

The rule is one statement at the host's start
(``host_dependencies.prepare_environment``): a host whose environment names a
store gives every child the setting that workers get from
``update_sources.store_environment``.

The case of the whole way uses the real helper file, the product's own start
of it, Foundation's real store, and a Git repository on this computer. It
sends nothing to a network. No case here skips on any platform.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from amplifier_web.host.config import HostConfig, merge
from amplifier_web.host_dependencies import prepare_environment
from amplifier_web.setup import SetupManager

HELPER = Path(__file__).resolve().parents[1] / 'amplifier_web' / 'provider_probe.py'
MODULE, PACKAGE, CONNECTION = 'provider-store-case', 'amplifier_module_provider_store_case', 'store-case-one'
FLAG = 'import sys; print(sys.dont_write_bytecode)'

# A provider module with two files, as a real one has. It says where Python
# loaded it from and whether that process writes bytecode.
FILES = {
    # The marker of a Python module for Foundation. There is no pyproject.toml,
    # so the helper installs nothing into the environment of these tests.
    'setup.cfg': '[metadata]\nname = amplifier-module-provider-store-case\n',
    '.gitattributes': '* -text\n',
    PACKAGE + '/_listing.py': (
        'def models(loaded_from, writes_bytecode):\n'
        "    return [{'id': 'store-case-model', 'loadedFrom': loaded_from, 'writesBytecode': writes_bytecode}]\n"),
    PACKAGE + '/__init__.py': (
        'import sys\n'
        'from ._listing import models\n\n\n'
        'class StoreCaseProvider:\n'
        '    def __init__(self, *, config=None):\n'
        '        self.config = config or {}\n\n'
        '    def get_info(self):\n'
        "        return {'id': 'store-case', 'display_name': 'Store case', 'config_fields': []}\n\n"
        '    def get_model_list_info(self):\n'
        "        return {'version': 1, 'source': 'static'}\n\n"
        '    async def list_models(self):\n'
        '        return models(__file__, not sys.dont_write_bytecode)\n'),
}


@pytest.fixture
def host_start(monkeypatch):
    """The environment of a host when it starts, then the host's own first step."""
    saved = dict(os.environ)

    def start(store):
        # A Windows installation's host gets the store's name from its task and
        # has no bytecode setting. A job or a shell that has one must not hide that.
        monkeypatch.delenv('PYTHONDONTWRITEBYTECODE', raising=False)
        if store is None:
            monkeypatch.delenv('AMPLIFIER_SOURCE_STORE', raising=False)
        else:
            monkeypatch.setenv('AMPLIFIER_SOURCE_STORE', str(store))
        prepare_environment()

    yield start
    # prepare_environment writes names itself (PATH, and more on Windows).
    os.environ.clear()
    os.environ.update(saved)


def child_flag():
    return subprocess.run([sys.executable, '-c', FLAG], capture_output=True, text=True, check=True).stdout.strip()


def test_a_host_that_names_a_source_store_gives_its_children_no_bytecode_writing(host_start, tmp_path):
    host_start(tmp_path / 's')
    assert os.environ.get('PYTHONDONTWRITEBYTECODE') == '1'
    assert child_flag() == 'True'


def test_a_host_without_a_source_store_keeps_its_environment(host_start):
    host_start(None)
    assert 'PYTHONDONTWRITEBYTECODE' not in os.environ
    assert child_flag() == 'False'


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


async def test_the_connection_check_leaves_the_store_object_as_foundation_published_it(
        host_start, tmp_path, monkeypatch):
    from amplifier_foundation import Bundle
    from amplifier_foundation.sources.shared import SharedSourceStore
    from amplifier_web.provider_isolation import ProviderIsolation

    source = repository(tmp_path / 'repository')
    home, workspace, store = tmp_path / 'app', tmp_path / 'workspace', tmp_path / 's'
    workspace.mkdir()
    manager = SetupManager(home, probe_command=[sys.executable, str(HELPER)])

    def config(folder):
        settings = {}
        for scope in ('global', 'project', 'local'):
            settings = merge(settings, manager.store.read(folder, scope))
        return HostConfig(home=manager.home, workspace=Path(folder), settings=settings,
                          registry_home=manager.home / 'foundation')
    monkeypatch.setattr(manager, 'config', config)
    connection = {'id': CONNECTION, 'module': MODULE, 'source': source, 'config': {}}
    manager.store.update(workspace, 'global', lambda settings: settings.update(config={'providers': [connection]}))

    # 1. The host starts. 2. The person selects "Save and check connection":
    # the product starts the real helper, which prepares the module from its
    # source and asks it for its models.
    host_start(store)
    checked = await manager.probe('providers.models', {'id': CONNECTION}, str(workspace))
    model = checked['models'][0]

    binding, = (home / 'foundation' / 'cache' / '.source-bindings').glob('*.json')
    record = json.loads(binding.read_text())
    shared = SharedSourceStore(store)
    published = shared.checkout(record['git_url'], record['commit'])
    try:
        await asyncio.to_thread(shared.verify, record['git_url'], record['commit'])
        verified = 'accepted'
    except ValueError as error:
        verified = f'refused: {error}'

    # 3. What a conversation worker does next: it prepares the same module
    # from the same source, with the host's rule for a provider that fails.
    monkeypatch.setattr(sys, 'path', list(sys.path))
    isolation = ProviderIsolation()
    provider = {'module': MODULE, 'source': source, 'config': {}}
    await Bundle.from_dict({'bundle': {'name': 'worker'}, 'providers': [provider]}).prepare(
        strict=True, install_deps=False, cache_dir=home / 'foundation' / 'cache',
        provider_failure_policy=isolation.preparation_failed)
    failure = isolation.failure(provider)

    assert {
        'the helper loaded the module from the store object': Path(model['loadedFrom']).parent.parent == published,
        'the helper writes bytecode': model['writesBytecode'],
        'bytecode in the object': sorted(path.relative_to(published).as_posix() for path in published.rglob('*')
                                         if path.name == '__pycache__' or path.suffix == '.pyc'),
        'Foundation, asked for the object again': verified,
        "the worker's record for the connection": failure and {
            name: failure.get(name) for name in ('reason_code', 'diagnostic')},
    } == {
        'the helper loaded the module from the store object': True,
        'the helper writes bytecode': False,
        'bytecode in the object': [],
        'Foundation, asked for the object again': 'accepted',
        "the worker's record for the connection": None,
    }
