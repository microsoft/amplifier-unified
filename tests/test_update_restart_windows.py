"""Windows refuses a forced restart before the update changes; the page gets no offer.

The native cases need no installation: they call the product's own consent
check and page view with the real platform. No app, installer or restart runs.
"""
import copy
import sys
from types import SimpleNamespace

import pytest

from amplifier_web import app_updates, update_health as health, update_restart


TARGET = {'hostInstanceId': 'host', 'revision': 'a' * 40, 'generation': 'b' * 32, 'attemptId': 'attempt'}
WORK = {'kind': 'conversation', 'sessionId': 'busy'}


class Untouched:
    """A manager that fails the case when anything reads it."""
    def __getattr__(self, name):
        raise AssertionError(f'A Windows forced restart read manager.{name} before it was refused')


def waiting_for_work():
    """A validated app that waits for a working chat, with the saved interrupt target."""
    return {'phase': 'app-staged', 'activation': 'waiting', 'blockers': [dict(WORK)],
            'pendingApp': {key: TARGET[key] for key in ('revision', 'generation', 'attemptId')},
            'application': {'canInstall': True}, 'forceRestartTarget': dict(TARGET)}


async def refused_first():
    with pytest.raises(update_restart.RestartRejected) as refused:
        await app_updates._activate_serialized(Untouched(), force={**TARGET, 'confirm': True})
    assert str(refused.value) == update_restart.WINDOWS_REFUSAL
    assert str(refused.value) == 'Amplifier on Windows cannot interrupt work to restart. The update was not changed.'


async def test_activation_refuses_a_windows_forced_restart_before_it_reads_anything(monkeypatch):
    """The platform is substituted here. The native case below uses the real one."""
    monkeypatch.setattr(update_restart, 'sys', SimpleNamespace(platform='win32'))
    await refused_first()


@pytest.mark.skipif(sys.platform == 'win32', reason='the other platforms keep the forced restart')
async def test_other_platforms_still_check_the_target_first():
    with pytest.raises(AssertionError, match='read manager'):
        await app_updates._activate_serialized(Untouched(), force={**TARGET, 'confirm': True})


@pytest.mark.skipif(sys.platform != 'win32', reason='requires the native Windows platform')
async def test_native_windows_forced_restart_is_refused_before_activation_reads_anything():
    await refused_first()


@pytest.mark.skipif(sys.platform != 'win32', reason='requires the native Windows platform')
def test_native_windows_page_view_has_no_interrupt_offer_and_the_saved_target_stays():
    state = waiting_for_work()
    before = copy.deepcopy(state)
    view = health.browser_view(state)
    assert view['forceRestartTarget'] is None
    # Nothing else changes for the page. The saved identity stays: task.waitForUpdate names it.
    assert {**view, 'forceRestartTarget': TARGET} == before and state == before
