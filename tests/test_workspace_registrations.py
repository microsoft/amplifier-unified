"""Registration metadata has one owner; host actions retain history and effects."""
import copy
import uuid

import pytest

from amplifier_web.workspace_registrations import edit, reconcile, register


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


def test_manual_edits_and_discovery_preserve_the_same_registration_objects():
    rows = [{'id': 'other', 'path': '/other', 'name': 'Other'}]
    candidate = {'id': 'project', 'path': '/project', 'name': 'Reports', 'available': True,
                 'nativeProject': 'project', 'aliases': ['Old name'], 'custom': {'color': 'blue'}}
    register(rows, candidate, name='Reports')
    candidate['available'] = False
    register(rows, {'id': 'project', 'path': '/project', 'name': 'project'}, name='')
    assert rows[1] is candidate and candidate['name'] == 'Reports' and candidate['available']
    assert edit(rows, 'workspace.rename', {'id': 'project', 'name': '  Weekly reports  '}) is candidate
    edit(rows, 'workspace.archive', {'id': 'project'})

    hidden = []
    result = reconcile(rows, [], [{'id': 'project', 'path': '/project', 'name': 'project',
                                  'nativeProject': 'project', 'available': True, 'sessionCount': 2}],
                       hidden_ids=hidden, managed_paths=set())
    assert result.by_id['project'] is candidate and rows[1] is candidate
    assert candidate == {'id': 'project', 'path': '/project', 'name': 'Weekly reports',
                         'available': True, 'nativeProject': 'project', 'aliases': ['Old name'],
                         'custom': {'color': 'blue'}, 'archived': True, 'sessionCount': 2}
    assert hidden == [] and result.remaps == []
    edit(rows, 'workspace.restore', {'id': 'project'})
    register(rows, {'id': 'project', 'path': '/project', 'name': 'Explicit'}, name='Explicit')
    assert candidate['name'] == 'Explicit' and candidate['archived'] is False
    assert edit(rows, 'workspace.remove', {'id': 'project'}) is candidate
    assert rows == [{'id': 'other', 'path': '/other', 'name': 'Other'}]


@pytest.mark.parametrize(('action', 'args', 'message'), [
    ('workspace.rename', {'id': 'missing'}, 'This workspace is no longer registered.'),
    ('workspace.rename', {'id': 'only', 'name': '   '}, 'Enter a workspace name.'),
    ('workspace.remove', {'id': 'only'}, 'Keep at least one workspace registered.'),
])
def test_manual_metadata_errors_preserve_records_and_lookup_precedence(action, args, message):
    row = {'id': 'only', 'path': '/project', 'name': 'Reports', 'custom': {'kept': True}}
    before = copy.deepcopy(row)
    rows = [row]
    with pytest.raises(ValueError) as exc:
        edit(rows, action, args)
    assert str(exc.value) == message
    assert rows == [before] and rows[0] is row


async def test_host_manual_actions_and_native_observation_preserve_history(tmp_path):
    import asyncio
    import json

    from amplifier_web.service import AppError, AppService
    from amplifier_web.session_files import amplifier_home, project_slug

    initial = tmp_path / 'initial'; initial.mkdir()
    folder = tmp_path / 'reports'; folder.mkdir()
    native_id = str(uuid.uuid4())
    native = amplifier_home() / 'projects' / project_slug(folder) / 'sessions' / native_id
    native.mkdir(parents=True)
    (native / 'metadata.json').write_text(json.dumps({'session_id': native_id, 'working_dir': str(folder),
                                                    'name': 'Native conversation', 'turn_count': 1}))
    transcript = native / 'transcript.jsonl'
    original = json.dumps({'role': 'user', 'content': 'Retained native history'}) + '\n'
    transcript.write_text(original)
    service = AppService(tmp_path / 'data', workspace=initial)
    try:
        first = service.state['workspaces'][0]
        for action, args, message in [
            ('workspace.rename', {'id': 'missing', 'name': '   '}, 'This workspace is no longer registered.'),
            ('workspace.rename', {'id': first['id'], 'name': '   '}, 'Enter a workspace name.'),
            ('workspace.remove', {'id': first['id']}, 'Keep at least one workspace registered.'),
        ]:
            with pytest.raises(AppError) as exc:
                await service.dispatch(action, args, origin='agent')
            assert exc.value.status == 400 and str(exc.value) == message

        await service.dispatch('workspace.add', {'path': str(folder), 'name': 'Reports'}, origin='agent')
        await asyncio.gather(*list(service.tasks))
        identity = service.state['selectedWorkspaceId']
        await service.dispatch('workspace.rename', {'id': identity, 'name': 'Weekly reports'})
        await service.dispatch('workspace.archive', {'id': identity})
        await service.history.refresh(force=True)
        row = next(row for row in service.state['workspaces'] if row['id'] == identity)
        assert row['name'] == 'Weekly reports' and row['path'] == str(folder) and row['archived']
        assert service.directory.resolve(native_id, project_slug(folder))['nativeIdentity'] == native_id
        await service.dispatch('workspace.restore', {'id': identity})
        await service.dispatch('workspace.add', {'path': str(folder / '.')})
        row = next(row for row in service.state['workspaces'] if row['id'] == identity)
        assert row['name'] == 'Weekly reports' and row['archived'] is False
        await service.dispatch('workspace.remove', {'id': identity})
        assert service.state['settings']['workspace'] == str(initial)
        assert transcript.read_text() == original
    finally:
        await service.close()

    restored = AppService(tmp_path / 'data')
    try:
        await restored.history.refresh(force=True)
        assert all(row['id'] != identity for row in restored.state['workspaces'])
        assert restored.state['settings']['workspace'] == str(initial)
        assert transcript.read_text() == original
    finally:
        await restored.close()
