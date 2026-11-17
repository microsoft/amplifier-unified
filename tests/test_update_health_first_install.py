"""Update health of a new installation, through the real update manager.

The installation has setup's record and no component generation (see
test_update_health_setup_record.py for the record and its limits). The real
service, update manager, application check and status projection run; only
external answers are injected.
"""
import json

import pytest

import amplifier_web
from amplifier_web import runtime_environment as environments
from amplifier_web import runtime_registry_updates as registry
from amplifier_web import update_health as health
from amplifier_web import update_report
from amplifier_web.updates import active_release
from test_update_core_lookup_failure import certificate_failure, checked_install
from test_update_health_setup_record import (INCOMPLETE, UP_TO_DATE, replace_package,
                                             setup_record)  # noqa: F401


def core_row(install):
    return next(row for row in environments.inventory(install.home) if row.get('package') == registry.CORE)


async def health_proof(manager):
    return (await update_report.collect(manager))['updateState']['healthProof']


async def test_a_new_installation_on_the_newest_release_is_up_to_date(setup_record, monkeypatch):  # noqa: F811
    assert core_row(setup_record)['registryManaged'] is True
    async with checked_install(setup_record.home, core_row(setup_record), monkeypatch) as c:
        await c.manager.check()
        assert c.state['application']['status'] == 'current'
        assert [row['status'] for row in c.manager.inventory] == ['current']
        assert c.state['phase'] == 'checked' and c.state['sequence']['stage'] == 'complete'
        status = c.state['currentStatus']
        assert (status['status'], status['title'], status['detail']) == ('current', 'You\u2019re up to date', UP_TO_DATE)
        assert status['actionRequired'] is False and status['action'] is None and status['component'] is None
        assert not active_release(setup_record.home)  # The check selected and prepared nothing.
        assert await health_proof(c.manager) == 'setup-record'  # Saved diagnostics name the proof.


async def test_setups_record_does_not_hide_a_source_that_cannot_be_checked(setup_record, monkeypatch):  # noqa: F811
    async with checked_install(setup_record.home, core_row(setup_record), monkeypatch) as c:
        c.index.side_effect = certificate_failure()
        await c.manager.check()
        status = c.state['currentStatus']
        assert status['title'] == 'A source could not be checked' and status['component'] == 'amplifier-core'
        assert status['actionRequired'] is True and c.manager.inventory[0]['status'] == 'check_failed'
        assert c.state['available'] == 0 and not c.state.get('includedCanProceed')


async def test_each_check_reads_the_record_again_and_preparation_never_uses_it(setup_record, monkeypatch):  # noqa: F811
    from amplifier_web.update_partial import Preparation
    async with checked_install(setup_record.home, core_row(setup_record), monkeypatch) as c:
        await c.manager.check()
        assert c.state['currentStatus']['detail'] == UP_TO_DATE
        # Setup's record is not a verified working generation for partial preparation.
        stage = c.manager.directory / 'releases' / ('c' * 32)
        with pytest.raises(ValueError, match='verified working component generation'):
            await Preparation(c.manager, stage, []).verify_baseline()
        assert not stage.exists() and not active_release(setup_record.home)

        replace_package(setup_record, 'PyYAML', '6.0.3', '6.0.4')
        await c.manager.check()
        status = c.state['currentStatus']
        assert (status['status'], status['title'], status['detail']) == (
            'unverified', 'Component verification is incomplete', INCOMPLETE)
        assert status['actionRequired'] is True
        assert await health_proof(c.manager) == 'none'


TARGET = 'd' * 32


async def first_candidate(c, install, promote):
    """A staged, validated first candidate. `promote` stands in for the worker promotion.

    This is not a qualified generation. The questions here are what is shown,
    and which proof is held, from the moment the pointer names it.
    """
    await c.manager.check()
    assert c.state['currentStatus']['detail'] == UP_TO_DATE and await health_proof(c.manager) == 'setup-record'
    stage = c.manager.directory / 'releases' / TARGET
    stage.mkdir(parents=True)
    (stage / 'validated.json').write_text(json.dumps(
        {'generationSchema': 1, 'workerProtocol': 1, 'createdAt': 1, 'hostVersion': amplifier_web.__version__}))
    c.service.runtime.promote_generation = promote
    await c.manager.publish(pendingRelease=TARGET)


async def test_the_first_activation_never_shows_setups_proof_for_the_new_generation(setup_record, monkeypatch):  # noqa: F811
    """The real activation: every publication after the pointer names a generation is inspected."""
    async with checked_install(setup_record.home, core_row(setup_record), monkeypatch) as c:
        during = []

        def promote(generation, protected=None):
            # The pointer is written; the installed state is not published yet.
            during.append((active_release(setup_record.home).get('current'), c.state.get('release'),
                           health.cached_evidence(c.manager)))
        await first_candidate(c, setup_record, promote)

        seen = []
        publish = c.service._publish_changes

        def observe(*args, **kwargs):
            seen.append((active_release(setup_record.home).get('current'), dict(c.state.get('currentStatus') or {})))
            return publish(*args, **kwargs)
        monkeypatch.setattr(c.service, '_publish_changes', observe)
        await c.manager.activate()

        assert during == [(TARGET, None, {})]  # During the promotion: selected, not published, no setup proof.
        assert active_release(setup_record.home)['current'] == TARGET and c.state['phase'] == 'installed'
        selected = [status for generation, status in seen if generation == TARGET]
        assert len(selected) >= 2  # The installed state, and the state after the refreshed proof.
        for status in selected:
            assert status.get('detail') != UP_TO_DATE, status
        assert c.state['currentStatus']['title'] == 'Component verification is incomplete'
        assert await health_proof(c.manager) == 'none'


async def test_a_first_promotion_that_fails_after_the_pointer_write_leaves_no_setup_proof(setup_record, monkeypatch):  # noqa: F811
    async with checked_install(setup_record.home, core_row(setup_record), monkeypatch) as c:
        def promote(generation, protected=None):
            raise RuntimeError('SECRET promotion detail')
        await first_candidate(c, setup_record, promote)
        await c.manager.activate()

        assert active_release(setup_record.home)['current'] == TARGET  # Written before the promotion.
        assert c.state['phase'] == 'error' and not c.state.get('release')  # No selection was published.
        status = c.state['currentStatus']
        assert status['actionRequired'] is True and status['status'] != 'current'
        assert status.get('detail') != UP_TO_DATE
        assert health.cached_evidence(c.manager) == {}
        assert await health_proof(c.manager) == 'none'  # Saved diagnostics claim no setup proof.
        assert 'SECRET' not in json.dumps(c.state)
