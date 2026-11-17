"""A validation at activation that failed is not repeated by time alone.

A prepared component update that another app version validated is validated
again at activation (``UpdateManager._activate``): minutes of probe processes.
When that validation failed, the update owner's pass came back to it at every
pass, about once a minute, for as long as the app ran.

The owner now comes back only when something that can change the result has
changed: another prepared update, the settings or the selection, the person's
keys file, a person's own request, or a new start of the app.

Synthetic files only; no app, probe or model is started.
"""
import asyncio
import json
import time
from types import SimpleNamespace

import pytest
import yaml

from amplifier_web.host.config import amplifier_home
from amplifier_web.update_diagnostics import CommandFailure
from test_update_stage_keys import stage_at
from test_updates import app, prepare, repo  # noqa: F401 -- existing real manager fixture


@pytest.fixture
async def prepared(app, repo, monkeypatch):
    """A prepared update that needs a validation at activation, and a validation that fails."""
    manager, _ = await prepare(app, repo)
    manager._storage_last_pass = time.time()   # The owner's pass starts no storage maintenance.
    state = app.state['updates']
    calls, outcome = [], {'error': 'command'}

    def stage(name):
        made = stage_at(manager.home, name)
        (made / 'validated.json').write_text(json.dumps({'hostVersion': 'old-app', 'sources': 1}))
        return made

    first = stage('a' * 32)
    state.update(phase='staged', pendingRelease=first.name)

    async def probe():
        raise CommandFailure({'exitCode': 1, 'durationMs': 5, 'stdoutBytes': 0, 'stderrBytes': 9})

    def failing(owner):
        """Give an update owner the validation of this case."""
        async def validate(target, release):
            calls.append(release)
            if outcome['error'] == 'cancelled':
                raise asyncio.CancelledError
            if outcome['error'] == 'command':
                # As the real validation: one of its recorded commands ends with an error.
                await owner.diagnostics.run('ecosystem-compatibility', probe)
            if outcome['error']:
                raise ValueError('No app-level conversation profiles are configured')

        monkeypatch.setattr(owner, 'validate', validate)
        owner._storage_last_pass = time.time()   # Its pass starts no storage maintenance.
        return owner

    failing(manager)

    async def passes(count, owner=manager):
        """An update owner's loop, for ``count`` passes. It raises nothing."""
        waits = []

        async def wait(delay):
            waits.append(delay)
            if len(waits) > count:
                owner.closed = True

        monkeypatch.setattr(owner, 'wait_for_work', wait)
        await owner.loop()
        owner.closed = False
        assert waits == [3] + [60] * count

    def occurrences():
        return [group['occurrences'] for group in manager.diagnostics.state.get('activeFailures', {}).values()]

    return SimpleNamespace(manager=manager, state=state, stage=stage, first=first, calls=calls, failing=failing,
                           outcome=outcome, passes=passes, occurrences=occurrences)


async def test_time_alone_does_not_repeat_a_failed_validation_at_activation(prepared):
    p = prepared
    await p.passes(5)
    # One validation, one failed attempt on the page's record, and the update stays prepared.
    assert p.calls == [p.first.name]
    assert p.occurrences() == [1]
    assert p.state['pendingRelease'] == p.first.name and p.state['phase'] == 'error'
    status = p.state['currentStatus']
    assert (status['status'], status['title'], status['action']) == (
        'action-required', 'Update needs attention', 'diagnostics')
    await p.passes(5)
    assert p.calls == [p.first.name] and p.occurrences() == [1]


@pytest.mark.parametrize('error', ['command', 'other'])
async def test_each_kind_of_failed_validation_is_held(prepared, error):
    """An error of a recorded command, and an error outside one."""
    p = prepared
    p.outcome['error'] = error
    await p.passes(3)
    assert p.calls == [p.first.name]


def live_keys(text):
    (amplifier_home() / 'keys.env').write_text(text)


def live_settings(change):
    path = amplifier_home() / 'settings.yaml'
    settings = yaml.safe_load(path.read_text())
    change(settings)
    path.write_text(yaml.safe_dump(settings))


CHANGES = {
    # The person adds a key, or changes one. The validation reads the live keys file.
    'keys-file-added': lambda p: live_keys('FIXTURE_KEY=synthetic-one\n'),
    # The person changes what the validation qualifies: a module's configuration.
    'settings': lambda p: live_settings(lambda settings: settings['config']['providers'][0]['config'].update(
        default_model='fixture-other-model')),
    # The person changes the selection of components.
    'selection': lambda p: live_settings(lambda settings: settings.setdefault('sources', {}).setdefault(
        'modules', {}).update({'tool-fixture': 'git+https://example.invalid/tool-fixture@main'})),
}


@pytest.mark.parametrize('change', sorted(CHANGES))
async def test_a_changed_input_gives_one_more_validation(prepared, change):
    p = prepared
    await p.passes(3)
    assert p.calls == [p.first.name]
    CHANGES[change](p)
    await p.passes(3)
    # Exactly one more, and it is held again: it failed with these inputs too.
    assert p.calls == [p.first.name] * 2
    await p.passes(3)
    assert p.calls == [p.first.name] * 2


async def test_a_keys_file_that_changes_again_gives_one_more_validation_each_time(prepared):
    p = prepared
    live_keys('FIXTURE_KEY=synthetic-one\n')
    await p.passes(2)
    live_keys('FIXTURE_KEY=synthetic-two-longer\n')   # another size: a change that needs no clock
    await p.passes(2)
    (amplifier_home() / 'keys.env').unlink()
    await p.passes(2)
    assert p.calls == [p.first.name] * 3


async def test_another_prepared_update_is_validated(prepared):
    p = prepared
    await p.passes(3)
    second = p.stage('b' * 32)
    p.state.update(pendingRelease=second.name)
    await p.passes(3)
    assert p.calls == [p.first.name, second.name]


async def test_a_persons_request_validates_again_and_time_alone_still_does_not(prepared):
    p = prepared
    await p.passes(3)
    await p.manager.command('install', None, 'c' * 32)   # It raises nothing.
    assert p.calls == [p.first.name] * 2
    # The request ends with a wake of the owner. That pass, and the later ones, validate nothing.
    await p.passes(3)
    assert p.calls == [p.first.name] * 2


async def test_a_new_start_of_the_app_validates_once_more(prepared):
    """Nothing about the hold is saved: the update owner of a new start of the app has none."""
    from amplifier_web.updates import UpdateManager

    p = prepared
    await p.passes(3)
    assert p.calls == [p.first.name]
    # As server.py makes the owner at a start of the app, over the same saved state and files.
    restarted = p.failing(UpdateManager(p.manager.service))
    assert p.state['pendingRelease'] == p.first.name
    await p.passes(3, restarted)
    assert p.calls == [p.first.name] * 2


async def test_a_stopped_validation_is_not_a_failed_one(prepared):
    """A cancellation says nothing about the update: the next pass validates."""
    p = prepared
    p.outcome['error'] = 'cancelled'
    with pytest.raises(asyncio.CancelledError):
        await p.manager.activate()
    p.outcome['error'] = 'command'
    await p.passes(1)
    assert p.calls == [p.first.name] * 2


async def test_a_validation_that_passes_ends_the_hold(prepared):
    p = prepared
    await p.passes(2)
    assert p.manager._activation_held(p.first.name) is True
    p.outcome['error'] = None
    await p.manager.command('install', None, 'c' * 32)   # The synthetic stage fails later; not this case.
    assert p.calls == [p.first.name] * 2
    marker = json.loads((p.first / 'validated.json').read_text())
    assert marker['hostVersion'] == __import__('amplifier_web').__version__
    assert p.manager._activation_held(p.first.name) is False


async def test_settings_that_cannot_be_read_do_not_make_the_owner_fail_or_validate_every_pass(prepared):
    p = prepared
    await p.passes(2)
    events = len(p.manager.diagnostics.state['events'])
    (amplifier_home() / 'settings.yaml').write_text('config: [unclosed')
    await p.passes(3)
    # One more validation for the change, then held; the owner's pass itself recorded no failure of its own.
    assert p.calls == [p.first.name] * 2
    assert [event['phase'] for event in p.manager.diagnostics.state['events'][events:]
            if event['phase'] == 'background-update'] == []
