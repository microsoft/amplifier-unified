from amplifier_web.browser_workers import recovery_workers
from amplifier_web.session_identity import project


def notice(call):
    return {'observation': {'source': 'local-job-recovery', 'recovery': {'call_id': call}}}


def test_recovery_receipt_retains_scoped_session_identity_without_catalog():
    session = {'id': 'parent', 'workspace': '/owned/project', 'workers': [
        {'id': 'native-child', 'kind': 'session', 'callId': 'session-call', 'name': 'Child', 'report': 'private body'},
        {'id': 'job-only', 'kind': 'job', 'callId': 'job-call'},
        {'id': 'display', 'sessionId': 'native-legacy', 'callId': 'legacy-call'},
    ]}
    messages = [notice(call) for call in ('session-call', 'job-call', 'legacy-call', 'unknown')]
    recovery_workers(session, messages)
    assert messages[0]['recoveryWorker'] == {'id': 'native-child', 'kind': 'session', 'name': 'Child', 'nativeProject': project(session)}
    assert messages[1]['recoveryWorker'] == {'id': 'job-only', 'kind': 'job'}
    assert messages[2]['recoveryWorker'] == {'id': 'display', 'sessionId': 'native-legacy', 'nativeProject': project(session)}
    assert 'recoveryWorker' not in messages[3]
    assert session['workers'][0]['report'] == 'private body'


def test_recovery_receipt_uses_canonical_project_not_execution_folder():
    session = {'id': 'parent', 'nativeProject': 'saved-project', 'workspace': '/moved/execution',
               'workers': [{'id': 'native-child', 'kind': 'session', 'callId': 'call'}]}
    messages = [notice('call')]
    recovery_workers(session, messages)
    assert messages[0]['recoveryWorker']['nativeProject'] == 'saved-project'
