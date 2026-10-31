"""An agent read prepares retained metadata once per synchronous projection."""
from copy import deepcopy

import pytest

from test_shell_modules import service
from test_cold_display import app, add_history


@pytest.mark.parametrize('retained', [1, 90])
@pytest.mark.parametrize('args,preparations', [({'path': '/revision'}, 1), ({}, 2)])
async def test_agent_read_does_not_recopy_questions_for_each_derived_section(
        service, retained, args, preparations):
    visits = []

    class Questions(list):
        def __deepcopy__(self, memo):
            visits.extend(item['id'] for item in self)
            return deepcopy(list(self), memo)

    row = service._state['sessions'][1]
    row['questions'] = Questions([
        {'id': f'q-{n}', 'status': 'answered', 'required': True,
         'revision': 1, 'prompt': 'Retained resolved question. ' * 100}
        for n in range(retained)])
    original = deepcopy(list(row['questions']))
    service._state['selectedSessionId'] = 'alpha'
    service._state['selectedWorkspaceId'] = 'one'

    result = await service.app_bridge('get_state', args, 'alpha')

    if args:
        assert result['value'] == service._state['revision']
    else:
        assert result['session']['id'] == 'alpha'
        assert {row['id'] for row in result['conversations']} == {'alpha', 'beta'}
    # Overview also takes an independent frozen directory snapshot. Its own
    # preparation is legitimate; repeating it for every derived section is not.
    assert len(visits) <= retained * preparations
    assert row['questions'] == original


async def test_new_agent_read_sees_nested_changes_without_publication(service):
    row = service._state['sessions'][1]
    row['questions'] = [{'id': 'question', 'status': 'pending', 'required': True,
                         'revision': 1, 'prompt': 'Choose a scope'}]
    revision = service._state['revision']

    async def notices():
        # The mutation owner invalidates derived views; neither a publication
        # nor a new app revision is needed to observe current metadata.
        service.projections.invalidate(state=service.state, session_ids={'beta'})
        return await service.app_bridge('get_state', {'path': '/attention/unread'}, 'alpha')

    assert (await notices())['value'] == 1
    row['questions'][0]['status'] = 'answered'
    assert (await notices())['value'] == 0
    row['questions'][0].update(status='pending', prompt='Choose the revised scope', revision=2)
    assert (await notices())['value'] == 1
    assert service._state['revision'] == revision


def test_complete_compatibility_read_still_hydrates_outside_read_frame(app, monkeypatch):
    row = add_history(app, 'complete-read')
    expected = deepcopy(row['messages'])
    monkeypatch.setattr(app.cold_display, 'RECENT_LIMIT', 0)
    app.cold_display.retire(force=True)
    assert app.cold_display.is_cold(row)
    from amplifier_web import session_projection
    original = session_projection.hydrate
    hydrated = []

    def hydrate(*args):
        assert app.directory._read_frame.get() is None
        hydrated.append(True)
        return original(*args)

    monkeypatch.setattr(session_projection, 'hydrate', hydrate)
    result = app.get_state()
    actual = next(owner for owner in result['sessions'] if owner['id'] == row['id'])
    assert hydrated
    assert actual['messages'] == expected
