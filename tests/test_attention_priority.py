"""Information is not work; observing an update failure is not recovery."""
import copy
from types import SimpleNamespace

import pytest

from amplifier_web.attention import snapshot
from amplifier_web.update_diagnostics import UpdateDiagnostics


def fixture(tmp_path, updates=None):
    state = {'attentionRead': {}, 'updates': updates or {}, 'feedback': {'requests': []}}
    service = SimpleNamespace(state=state, _save_changes=lambda **kwargs: None)
    diagnostics = UpdateDiagnostics(SimpleNamespace(service=service, directory=tmp_path))
    return state, diagnostics


def fail(diagnostics, *, component='amplifier-module-loop-live', kind='ecosystem'):
    diagnostics.begin(kind, 'c' * 40, components=[component], application_extras=[] if kind == 'application' else None)
    return diagnostics.record(kind + '-validation', 'failed', component=component,
                              reason='runtime-resolution-mismatch', errorType='ValueError',
                              expectedRevision='a' * 40, observedRevision='b' * 40)


def active(state):
    return [item for item in snapshot(state)['items'] if item.get('actionRequired')]


def read_all(state):
    state['attentionRead'].update({i['id']: i['fingerprint'] for i in snapshot(state)['items']})


def test_news_and_success_receipts_are_quiet_but_retained(tmp_path):
    state, _ = fixture(tmp_path)
    state['updates']['application'] = {'releaseNotes': [{'version': '1.0.0', 'notices': [
        {'id': str(i), 'title': f'News {i}', 'detail': 'Optional guidance.',
         'action': 'No action is required for ordinary browser use.'} for i in range(53)]}]}
    state['feedback']['requests'] = [{'requestId': f'sent-{i}', 'status': 'submitted',
                                     'title': 'Reported', 'message': 'Feedback sent.'} for i in range(3)]
    before = copy.deepcopy(state)
    result = snapshot(state)
    assert len(result['items']) == result['unread'] == 56  # Seen state is still truthful.
    assert all(i['informational'] for i in result['items'])
    assert result['badgeUnread'] == result['settingsUnread'] == 0
    assert result['sections']['maintenance'] == result['sections']['feedback'] == 0
    assert state == before
    state['feedback']['requests'][0]['status'] = 'failed'
    assert snapshot(state)['badgeUnread'] == 1  # Not blanket badge suppression.


def test_read_repeat_distinct_recovery_and_next_failure(tmp_path):
    state, diagnostics = fixture(tmp_path)
    original = fail(diagnostics)
    first = active(state)[0]
    read_all(state)
    assert active(state)[0]['read'] is True
    assert snapshot(state)['badgeUnread'] == snapshot(state)['settingsUnread'] == 1
    repeated = fail(diagnostics)
    group = active(state)[0]
    assert group['fingerprint'] == first['fingerprint']
    assert group['read'] and group['occurrences'] == 2
    assert len(active(state)) == 1
    assert original in diagnostics.state['events'] and repeated in diagnostics.state['events']
    distinct = fail(diagnostics, component='amplifier-module-hooks-approval')
    assert len(active(state)) == 2
    assert not active(state)[0]['read']
    assert active(state)[0]['fingerprint'] != first['fingerprint']
    fail(diagnostics)  # A -> B -> A: B stays first; A retains its original read identity.
    assert active(state)[1]['fingerprint'] == first['fingerprint']
    assert active(state)[1]['occurrences'] == 3 and active(state)[1]['read']
    read_all(state)
    # Checking, preparation and clearing a transient error are NOT recovery.
    state['updates'].update(phase='checking', error=None)
    diagnostics.begin('ecosystem', components=['amplifier-module-loop-live','amplifier-module-hooks-approval'])
    diagnostics.clear_failure()
    diagnostics.record('ecosystem-stage', 'succeeded')
    assert len(active(state)) == 2
    assert snapshot(state)['badgeUnread'] == 2
    diagnostics.record('ecosystem-activation', 'succeeded')
    assert active(state) == []
    assert snapshot(state)['badgeUnread'] == 0
    assert distinct in diagnostics.state['events']
    fail(diagnostics, component='amplifier-module-hooks-approval')
    assert len(active(state)) == 1 and not active(state)[0]['read']


@pytest.mark.parametrize('phase', ['restart-ack', 'restart-reconcile', 'restart-superseded'])
def test_application_success_does_not_resolve_ecosystem_failure(tmp_path, phase):
    state, diagnostics = fixture(tmp_path)
    fail(diagnostics)
    fail(diagnostics, kind='application')
    assert len(active(state)) == 2
    diagnostics.begin('application', 'c' * 40, application_extras=[])
    diagnostics.clear_failure()
    diagnostics.record(phase, 'succeeded')
    assert len(active(state)) == 1
    assert active(state)[0]['updateKind'] == 'ecosystem'
    assert 'Current components remain active' in active(state)[0]['detail']


def test_legacy_previously_read_failure_is_migrated_only_with_active_evidence(tmp_path):
    failure = {'id': 'old', 'kind': 'ecosystem', 'attemptId': 'old-attempt',
               'phase': 'ecosystem-validation', 'status': 'failed', 'reason': 'runtime-resolution-mismatch'}
    updates = {'phase': 'error', 'error': 'Component preparation failed.',
               'diagnostics': {'events': [failure], 'lastFailure': failure}}
    state, _ = fixture(tmp_path, updates)
    read_all(state)
    assert len(active(state)) == 2 and snapshot(state)['badgeUnread'] == 2
    # A healthy old installation may retain lastFailure solely as history.
    app_failure = {**failure, 'kind': 'application', 'phase': 'restart-request'}
    healthy, _ = fixture(tmp_path, {'phase': 'interrupted',
        'reconciliation': {'attemptId': 'old-attempt', 'verifiedAt': 10},
        'diagnostics': {'events': [app_failure], 'lastFailure': app_failure}})
    assert active(healthy) == []
    # An error without a structured receipt still needs attention.
    plain, _ = fixture(tmp_path, {'phase': 'error', 'error': 'Check failed.'})
    read_all(plain)
    assert snapshot(plain)['badgeUnread'] == 1


def test_preference_and_projection_cost_do_not_turn_history_into_badge_debt(tmp_path):
    state, diagnostics = fixture(tmp_path)
    state['sessions'] = [{'id': 'chat', 'title': 'Synthetic', 'completion': {'id': 'one'}}]
    fail(diagnostics)
    for enabled, expected in [(False, 1), (True, 2)]:
        state['notificationSettings'] = {'completionBadge': enabled}
        assert snapshot(state)['badgeUnread'] == expected
    assert snapshot(state)['sections']['chats'] == 1


def test_other_tier_and_smart_tool_skip_are_not_component_recovery(tmp_path):
    state, diagnostics = fixture(tmp_path)
    diagnostics.begin('ecosystem', tier='included', components=['component-a'])
    diagnostics.record('ecosystem-validation', 'failed', errorType='ValueError')
    diagnostics.begin('smart-tools')
    diagnostics.record('smart-tools-complete', 'succeeded')
    assert len(active(state)) == 1
    diagnostics.begin('ecosystem', tier='other', components=['component-a'])
    diagnostics.record('ecosystem-activation', 'succeeded')
    assert len(active(state)) == 1
    diagnostics.begin('ecosystem', tier='included', components=['component-a'])
    diagnostics.record('ecosystem-activation', 'succeeded')
    assert active(state) == []


def test_new_check_error_is_not_hidden_by_a_reviewed_component_failure(tmp_path):
    state, diagnostics = fixture(tmp_path)
    fail(diagnostics)
    read_all(state)
    state['updates']['error'] = 'The application release could not be checked.'
    items = active(state)
    assert len(items) == 2 and items[0]['id'] == 'updates:error' and not items[0]['read']
    assert items[1]['read']


def test_repeat_keeps_original_evidence_beyond_the_recent_event_window(tmp_path):
    import json
    state, diagnostics = fixture(tmp_path)
    first = fail(diagnostics)
    read_all(state)
    for _ in range(55):
        diagnostics.record('ecosystem-check', 'succeeded')
    fail(diagnostics)
    assert active(state)[0]['firstFailure'] == first
    assert active(state)[0]['occurrences'] == 2 and active(state)[0]['read']
    assert first not in diagnostics.state['events']
    originals = [json.loads(line) for line in diagnostics.path.read_text().splitlines()]
    assert originals[0] == first


def test_matching_scope_and_order_are_required_to_settle(tmp_path):
    state, diagnostics = fixture(tmp_path)
    fail(diagnostics, component='amplifier-module-a')
    diagnostics.begin('ecosystem', components=['amplifier-module-b'])
    diagnostics.record('ecosystem-activation', 'succeeded')
    assert len(active(state)) == 1
    diagnostics.begin('ecosystem', components=['amplifier-module-a'])
    diagnostics.record('ecosystem-validation', 'failed', component='amplifier-module-a',
                       reason='runtime-resolution-mismatch', errorType='ValueError',
                       expectedRevision='a'*40, observedRevision='b'*40)
    diagnostics.record('ecosystem-activation', 'succeeded')
    assert len(active(state)) == 1  # The completion cannot clear a newer occurrence.
    diagnostics.begin('ecosystem', components=['amplifier-module-a'])
    diagnostics.record('ecosystem-activation', 'succeeded')
    assert active(state) == []


def test_same_cause_in_different_tiers_recovers_independently(tmp_path):
    state, diagnostics = fixture(tmp_path)
    for tier in ('included', 'other'):
        diagnostics.begin('ecosystem', tier=tier, components=['component-a'])
        diagnostics.record('ecosystem-validation', 'failed', errorType='ValueError')
    assert len(active(state)) == 2
    diagnostics.begin('ecosystem', tier='included', components=['component-a'])
    diagnostics.record('ecosystem-activation', 'succeeded')
    assert len(active(state)) == 1
    assert next(iter(diagnostics.state['activeFailures'].values()))['tier'] == 'other'


def test_unqualified_failures_in_different_scopes_do_not_merge(tmp_path):
    state, diagnostics = fixture(tmp_path)
    for scope in ['component-a', 'component-b']:
        diagnostics.begin('ecosystem', components=[scope])
        diagnostics.record('ecosystem-validation', 'failed', errorType='ValueError')
    assert len(active(state)) == 2
    diagnostics.begin('ecosystem', components=['component-a'])
    diagnostics.record('ecosystem-activation', 'succeeded')
    assert len(active(state)) == 1
    assert next(iter(diagnostics.state['activeFailures'].values()))['components'] == ['component-b']


def test_original_public_cause_survives_a_metadata_only_collector(tmp_path):
    import json
    state, diagnostics = fixture(tmp_path)
    diagnostics.manager.service.diagnostics = SimpleNamespace(record=lambda *args: None)
    diagnostics.begin('ecosystem', components=['component-a'])
    event = diagnostics.record('ecosystem-validation', 'failed', errorType='ValueError',
                               failureLocation='amplifier_web.updates:validate:42')
    for _ in range(55):
        diagnostics.record('ecosystem-check', 'succeeded')
    assert event not in diagnostics.state['events']
    assert json.loads(diagnostics.path.read_text().splitlines()[0]) == event


@pytest.mark.parametrize('components', [[], [f'component-{i}' for i in range(6)]])
def test_unknown_or_truncated_scope_cannot_claim_recovery(tmp_path, components):
    state, diagnostics = fixture(tmp_path)
    diagnostics.begin('ecosystem', components=components)
    diagnostics.record('ecosystem-validation', 'failed', errorType='ValueError')
    diagnostics.begin('ecosystem', components=components)
    diagnostics.record('ecosystem-activation', 'succeeded')
    assert len(active(state)) == 1


def test_wrong_application_target_cannot_claim_recovery(tmp_path):
    state, diagnostics = fixture(tmp_path)
    fail(diagnostics, kind='application')
    diagnostics.begin('application', 'd' * 40, application_extras=[])
    diagnostics.record('restart-ack', 'succeeded')
    assert len(active(state)) == 1
    diagnostics.begin('application', 'c' * 40, application_extras=[])
    diagnostics.record('restart-ack', 'succeeded')
    assert active(state) == []


def test_migration_keeps_unrelated_current_error_and_ecosystem_failure(tmp_path):
    failure = {'id': 'old', 'kind': 'ecosystem', 'attemptId': 'old-attempt',
               'phase': 'ecosystem-validation', 'status': 'failed'}
    state, _ = fixture(tmp_path, {'phase': 'error', 'error': 'Application check failed.',
        'reconciliation': {'attemptId': 'old-attempt', 'verifiedAt': 10},
        'diagnostics': {'events': [failure], 'lastFailure': failure}})
    assert len(active(state)) == 2
    assert active(state)[0]['id'] == 'updates:error'


def test_distinct_code_location_and_bounded_unresolved_overflow(tmp_path):
    from amplifier_web.update_diagnostics import MAX_ACTIVE_FAILURES
    state, diagnostics = fixture(tmp_path)
    for i in range(MAX_ACTIVE_FAILURES + 4):
        diagnostics.begin('ecosystem', components=['component-a'])
        diagnostics.record('ecosystem-validation', 'failed', errorType='ValueError',
                           failureLocation=f'amplifier_web.updates:validate:{i+1}')
    assert len(diagnostics.state['activeFailures']) == MAX_ACTIVE_FAILURES
    assert len(active(state)) == MAX_ACTIVE_FAILURES + 1
    assert diagnostics.state['failureOverflow']['occurrences'] == 4
    read_all(state)
    assert snapshot(state)['badgeUnread'] == MAX_ACTIVE_FAILURES + 1
    diagnostics.begin('ecosystem', components=['component-a'])
    diagnostics.record('ecosystem-activation', 'succeeded')
    assert len(active(state)) == 1  # Unknown overflow is not silently evicted or resolved.
    assert active(state)[0]['id'] == 'updates:failure-overflow'

@pytest.mark.parametrize('unknown', [None, 'tui', ['tui', 'tui'], ['tui', '/private/secret'], ['unknown']])
def test_unknown_application_feature_scope_never_matches_known_base(tmp_path, unknown):
    import json
    from amplifier_web.update_report import event as public_event
    state, diagnostics = fixture(tmp_path)
    diagnostics.begin('application', 'a'*40, application_extras=unknown)
    event = diagnostics.record('candidate-install', 'failed', errorType='ValueError')
    assert 'applicationExtras' not in event
    diagnostics.begin('application', 'a'*40, application_extras=[])
    diagnostics.record('restart-ack', 'succeeded')
    assert len(active(state)) == 1
    assert 'applicationExtras' not in public_event({**event, 'applicationExtras': unknown})
    assert '/private/secret' not in json.dumps(diagnostics.state)


def test_legacy_updater_missing_feature_helper_keeps_absent_scope_unknown(tmp_path, monkeypatch):
    import sys
    from types import ModuleType
    from amplifier_web.update_diagnostics import sanitized_application_extras
    from amplifier_web.update_report import event as public_event

    # The systemd journey runs a historical app_updates module alongside the
    # current diagnostics owner. That module has no optional-feature validator.
    legacy = ModuleType('amplifier_web.app_updates')
    monkeypatch.setitem(sys.modules, legacy.__name__, legacy)
    assert not hasattr(legacy, 'validated_extras')
    assert sanitized_application_extras(None) is None
    state, diagnostics = fixture(tmp_path)
    diagnostics.begin('application', 'a' * 40)
    failure = diagnostics.record('service-restart', 'failed', errorType='RuntimeError')
    assert 'applicationExtras' not in failure
    assert 'applicationExtras' not in public_event(failure)
    diagnostics.begin('application', 'a' * 40)
    diagnostics.record('restart-ack', 'succeeded')
    assert len(active(state)) == 1  # Unknown never becomes a known base install.


def test_application_groups_keep_revision_and_exact_feature_scope(tmp_path):
    state, diagnostics = fixture(tmp_path)
    for revision, extras in [('a'*40, []), ('a'*40, ['tui']), ('b'*40, ['tui']),
                             ('a'*40, ['native-desktop', 'tui'])]:
        diagnostics.begin('application', revision, application_extras=extras)
        diagnostics.record('candidate-install', 'failed', errorType='ValueError')
    assert len(active(state)) == 4
    read_all(state)
    diagnostics.begin('application', 'a'*40, application_extras=['tui', 'native-desktop'])
    repeat = diagnostics.record('candidate-install', 'failed', errorType='ValueError')
    assert repeat['applicationExtras'] == ['native-desktop', 'tui']
    assert len(active(state)) == 4 and active(state)[0]['occurrences'] == 2 and active(state)[0]['read']
    diagnostics.begin('application', 'a'*40, application_extras=['tui'])
    diagnostics.record('restart-ack', 'succeeded')
    assert len(active(state)) == 3


async def test_real_attention_read_and_restart_preserve_unresolved_failure(tmp_path):
    from amplifier_web.service import AppService
    from amplifier_web.updates import UpdateManager
    app = AppService(tmp_path / 'app', workspace=tmp_path)
    try:
        app.update_manager = UpdateManager(app)
        fail(app.update_manager.diagnostics)
        item = active(app.state)[0]
        await app.dispatch('attention.read', {'ids': [item['id']],
                           'fingerprints': {item['id']: item['fingerprint']}})
        assert active(app.state)[0]['read']
        assert app.get_state()['attention']['badgeUnread'] == 1
        app._save_changes(globals={'updates', 'attentionRead'})
    finally:
        await app.close()
    app = AppService(tmp_path / 'app', workspace=tmp_path)
    try:
        assert active(app.state)[0]['read']
        assert app.get_state()['attention']['badgeUnread'] == 1
    finally:
        await app.close()
