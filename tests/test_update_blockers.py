from types import SimpleNamespace
from amplifier_web.update_blockers import blockers


def test_background_worker_links_parent_and_blocked_task_is_not_active():
    service = SimpleNamespace(state={'sessions': [
        {'id': 'parent', 'title': 'My chat', 'status': 'idle', 'task': {'status': 'blocked'}},
        {'id': 'worker', 'parentId': 'parent', 'status': 'working'}]})
    assert blockers(service) == [{'kind': 'conversation', 'label': 'Conversation work is running or finishing',
                                   'sessionId': 'parent', 'title': 'My chat'}]
    service.state['sessions'][1]['status'] = 'idle'
    assert blockers(service) == []


def test_persistent_idle_worker_and_nonchat_activity_are_explicit():
    service = SimpleNamespace(state={'sessions': [{'id': 'chat', 'workers': [{'persistent': True, 'status': 'idle'}]}],
                                     'smartTools': {'operations': [{'status': 'running'}]},
                                     'feedback': {'requests': [{'status': 'sending'}]}})
    reasons = blockers(service)
    assert {row['kind'] for row in reasons} == {'worker', 'smart-tools', 'feedback'}
    assert next(row for row in reasons if row['kind']=='worker')['sessionId'] == 'chat'


def test_pending_runtime_links_preparing_chat():
    service = SimpleNamespace(state={'sessions': [{'id': 'chat', 'title': 'Starting chat'}]},
        runtime=SimpleNamespace(has_pending_operations=lambda: True, pending_session_ids=lambda: ['chat']))
    assert blockers(service)[0]['title'] == 'Starting chat'
