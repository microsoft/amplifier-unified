"""Narrow read-only exception; all model/probe/runtime guards remain fail-closed."""
import asyncio
from collections import Counter
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys

import pytest


def load_media(monkeypatch):
    # Source-mode fixture imports prepend their checkout. Keep that precedence
    # local to this test, including when module execution fails.
    monkeypatch.setattr(sys, 'path', sys.path.copy())
    monkeypatch.setenv('INLINE_MEDIA_MODE', 'source')
    spec = importlib.util.spec_from_file_location(
        'inline_media_fixture_test', Path(__file__).parent / 'fixtures/inline_media_server.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'COUNTERS', Counter())
    monkeypatch.setattr(module, 'ERRORS', [])
    return module


@pytest.fixture
def media(monkeypatch):
    return load_media(monkeypatch)


def test_fixture_import_restores_python_path():
    original, entries = sys.path, sys.path.copy()
    with pytest.MonkeyPatch.context() as patch:
        load_media(patch)
        assert sys.path is not original
        assert sys.path[0] == str(Path(__file__).resolve().parents[1])
    assert sys.path is original
    assert sys.path == entries


@pytest.fixture
def guarded(media, monkeypatch, tmp_path):
    from amplifier_web import draft_defaults, automatic_naming, naming_backfill
    from amplifier_web.setup import SetupManager
    from amplifier_web.management import Management
    # Snapshot fixture-installed globals with pytest's restoration owner.
    for owner, name in (
        (draft_defaults, 'resolve_defaults'), (automatic_naming.AutomaticNaming, 'schedule'),
        (naming_backfill.NamingBackfill, 'start'), (SetupManager, 'perform'),
        (SetupManager, 'provider_rows'), (SetupManager, 'cached_probe'), (Management, 'perform'),
    ):
        monkeypatch.setattr(owner, name, getattr(owner, name))
    restore = media.isolate_probes(tmp_path)
    try:
        yield media
    finally:
        restore()


async def test_saved_suggestion_read_is_disabled_deterministic_and_has_no_probes(guarded):
    from amplifier_web.setup import SetupManager
    from amplifier_web.suggestion_provider import INSTRUCTION
    first = await SetupManager.perform(None, 'providers.suggestionModels', {})
    expected = {'suggestionModels': {
        'enabled': False, 'provider': None, 'model': None, 'effort': None,
        'prompt': INSTRUCTION, 'defaultPrompt': INSTRUCTION,
        'transcriptMode': 'last_n', 'humanTurns': 6,
        'revision': hashlib.sha256(b'inline-media-disabled-suggestions-v1').hexdigest(),
        'takesEffect': 'next_request',
    }, 'providers': []}
    assert first == expected
    first['suggestionModels']['enabled'] = True
    assert await SetupManager.perform(None, 'providers.suggestionModels', {}) == expected
    assert await SetupManager.perform(None, 'providers.list', {}) == {'providers': []}
    assert guarded.COUNTERS == {'intercept:providers.suggestionModels': 2, 'intercept:providers.list': 1}
    assert guarded.ERRORS == []


@pytest.mark.parametrize('action', [
    'providers.test', 'providers.models', 'providers.discover', 'providers.configureSuggestions',
    'providers.unknown', 'session.suggestion.request',
])
async def test_other_setup_actions_still_fail_and_are_recorded(guarded, action):
    from amplifier_web.setup import SetupManager
    kind = 'provider-operation:' + action
    with pytest.raises(AssertionError, match='Unexpected ' + kind):
        await SetupManager.perform(None, action, {})
    assert guarded.COUNTERS == {'intercept:' + action: 1, 'unexpected:' + kind: 1}
    assert guarded.ERRORS == ['Unexpected ' + kind]


def test_direct_provider_probe_is_forbidden(guarded):
    from amplifier_web.setup import SetupManager
    with pytest.raises(AssertionError, match='Unexpected provider-probe'):
        SetupManager.cached_probe(None, {})
    assert guarded.COUNTERS == {'unexpected:provider-probe': 1}
    assert guarded.ERRORS == ['Unexpected provider-probe']


@pytest.mark.parametrize('operation', ['start', 'send', 'control'])
async def test_runtime_execution_is_forbidden(media, operation):
    with pytest.raises(AssertionError, match='Unexpected runtime-' + operation):
        await getattr(media.NoModel(), operation)()
    assert media.COUNTERS == {'unexpected:runtime-' + operation: 1}
    assert media.ERRORS == ['Unexpected runtime-' + operation]


@pytest.mark.parametrize('fails', [False, True])
async def test_terminal_removal_receipt_preserves_primary_failure(media, monkeypatch, capsys, fails):
    roots = []
    failure = AssertionError('primary no-model failure')
    async def create(*args, **kwargs):
        return {}
    async def serve(root, app):
        roots.append(root)
        assert root.is_dir()
        if fails:
            raise failure
    monkeypatch.setattr(media, 'create_app', create)
    monkeypatch.setattr(media, 'serve', serve)
    monkeypatch.setattr(media, 'isolate_probes', lambda _: None)
    monkeypatch.setattr(media.auth, 'authenticate_pam', media.auth.authenticate_pam)
    for name in ('AMPLIFIER_HOME', 'AMPLIFIER_WEB_HOME', 'AMPLIFIER_SESSION_STATE_HOME',
                 'AMPLIFIER_UNIFIED_IMPORT_HOME'):
        monkeypatch.setenv(name, os.environ.get(name, ''))
    loop = asyncio.get_running_loop()
    handler = loop.get_exception_handler()
    try:
        if fails:
            with pytest.raises(AssertionError) as caught:
                await media.main()
            assert caught.value is failure
        else:
            await media.main()
    finally:
        loop.set_exception_handler(handler)
    assert len(roots) == 1 and not roots[0].exists()
    assert json.loads(capsys.readouterr().out) == {
        'phase': 'fixture-process', 'temporaryRemoved': True}