"""Registration reconciliation can run without a history manager or app state."""
import copy
import uuid

from amplifier_web.workspace_registrations import reconcile


def test_reconcile_preserves_owned_rows_and_reports_ordered_reference_remaps():
    original = {'id': 'old', 'nativeProject': 'project', 'name': 'Custom name',
                'path': None, 'favorite': {'color': 'blue'}}
    retained = {'id': 'retained', 'nativeProject': 'other', 'name': 'Other',
                'path': '/other', 'available': True}
    registrations = [original, retained]
    hidden_id = uuid.uuid5(uuid.NAMESPACE_URL, 'amplifier-project:hidden').hex
    hidden = [hidden_id]
    observations = [
        {'id': 'intermediate', 'nativeProject': 'project', 'name': 'project', 'path': None},
        {'id': 'resolved', 'nativeProject': 'project', 'name': 'project', 'path': '/project'},
        {'id': 'hidden-resolved', 'nativeProject': 'hidden', 'path': '/hidden'},
        {'id': 'managed', 'nativeProject': 'managed', 'path': '/managed'},
    ]
    observed_before = copy.deepcopy(observations)

    result = reconcile(registrations, [{'id': 'retained', 'path': '/other', 'available': False}],
                       observations, hidden_ids=hidden, managed_paths={'/managed'})

    assert result.remaps == [('old', 'intermediate', None), ('intermediate', 'resolved', '/project')]
    assert registrations[0] is retained and result.by_id['retained'] is retained
    assert retained['available'] is False
    assert [row['id'] for row in registrations] == ['retained', 'resolved']
    resolved = result.by_id['resolved']
    assert resolved is registrations[1] and resolved is not observations[1]
    assert resolved['name'] == 'Custom name'
    assert resolved['favorite'] == original['favorite']
    assert resolved['favorite'] is not original['favorite']
    assert hidden == [hidden_id, 'hidden-resolved']
    assert result.hidden_ids == set(hidden)
    assert result.registrations_changed and result.hidden_changed
    assert observations == observed_before

    unchanged = reconcile(registrations, [], observations[1:], hidden_ids=hidden, managed_paths={'/managed'})
    assert unchanged.remaps == []
    assert not unchanged.registrations_changed and not unchanged.hidden_changed
    assert registrations[0] is retained and registrations[1] is resolved
