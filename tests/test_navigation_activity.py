from amplifier_web.navigation_summary import activity


def test_running_task_is_visible_even_when_saved_task_remains_blocked():
    assert activity({'status': 'working'}, blocked=True)['kind'] == 'working'
    assert activity({'status': 'idle'}, blocked=True)['label'] == 'Work blocked'
    assert activity({'status': 'working', 'approvals': [{'status': 'pending'}]}, blocked=True)['label'] == 'Approval requested'
