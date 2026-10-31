"""Registration owns routing names; ordinary dispatch must not rebuild schemas."""
import copy

import pytest

from amplifier_web import registered_test_runners, service, task_environments
from amplifier_web import test_capacity_settings, test_execution_settings, workspace_resources
from test_configuration_authority import app


FACTORIES = (
    'resource_reservation_definitions', 'environment_definitions',
    'test_capacity_definitions', 'test_runner_definitions',
    'test_execution_definitions', 'test_queue_definitions',
)


def schema_build_counts(monkeypatch):
    counts = dict.fromkeys(FACTORIES, 0)
    for name in FACTORIES:
        original = getattr(service, name)

        def counted(*args, _name=name, _original=original, **kwargs):
            counts[_name] += 1
            return _original(*args, **kwargs)

        monkeypatch.setattr(service, name, counted)
    return counts


def observe_domains(app, monkeypatch):
    events = []
    capture = app.configuration_authority.capture

    def captured(action, *args):
        result = capture(action, *args)
        events.append(('capture', action))
        return result

    monkeypatch.setattr(app.configuration_authority, 'capture', captured)
    for name, owner, attribute, action_index in (
        ('runner', registered_test_runners, 'dispatch', 1),
        ('capacity', test_capacity_settings, 'dispatch', 1),
        ('execution', test_execution_settings, 'dispatch', 1),
        ('queue', app.test_queue, 'dispatch', 0),
        ('environment', task_environments, 'dispatch', 1),
        ('resource', workspace_resources, 'dispatch_reservation', 1),
    ):
        original = getattr(owner, attribute)

        async def observed(*args, _name=name, _original=original, _index=action_index, **kwargs):
            events.append((_name, args[_index]))
            return await _original(*args, **kwargs)

        monkeypatch.setattr(owner, attribute, observed)
    return events


@pytest.mark.parametrize('repetitions', [1, 32])
async def test_repeated_view_dispatch_does_not_build_unrelated_domain_schemas(app, monkeypatch, repetitions):
    counts = schema_build_counts(monkeypatch)
    history = [(row['id'], copy.deepcopy(row['messages'])) for row in app.state['sessions']]
    for index in range(repetitions):
        result = await app.dispatch('view.update', {'patch': {'navWidth': 300 + index}}, include_state=False)
        assert result['accepted'] is True
        assert app.state['view']['navWidth'] == 300 + index
    assert counts == dict.fromkeys(FACTORIES, 0)
    assert [(row['id'], row['messages']) for row in app.state['sessions']] == history
    assert app.runtime.calls == []


@pytest.mark.parametrize('action,domain,fields', [
    ('testing.runners.read', 'runner', ()),
    ('testing.capacity.read', 'capacity', ()),
    ('testing.execution.read', 'execution', ()),
    ('workspace.tests.read', 'queue', ('workspaceId', 'sessionId')),
    ('workspace.environments.read', 'environment', ('workspaceId', 'sessionId')),
    ('workspace.resources.reservations', 'resource', ('workspaceId',)),
])
async def test_registered_passive_action_keeps_its_domain_and_authority_order(app, monkeypatch, action, domain, fields):
    counts = schema_build_counts(monkeypatch)
    events = observe_domains(app, monkeypatch)
    source = app.state['sessions'][0]
    workspace = next(row for row in app.state['workspaces'] if row['path'] == source['workspace'])
    scope = {'workspaceId': workspace['id'], 'sessionId': source['id']}
    args = {key: scope[key] for key in fields}
    before = copy.deepcopy(args)
    first = await app.dispatch(action, args, include_state=False)
    second = await app.dispatch(action, args, include_state=False)
    assert first['accepted'] is True and second['accepted'] is True
    assert events == [('capture', action), (domain, action)] * 2
    assert args == before
    assert counts == dict.fromkeys(FACTORIES, 0)
    assert app.runtime.calls == []


async def test_unregistered_names_do_not_inherit_domain_membership(app, monkeypatch):
    counts = schema_build_counts(monkeypatch)
    events = observe_domains(app, monkeypatch)
    for action in ('testing.runners.unregistered', 'testing.capacity.unregistered',
                   'testing.execution.unregistered', 'workspace.tests.unregistered',
                   'workspace.environments.unregistered', 'workspace.resources.unregistered'):
        with pytest.raises(service.AppError, match='Unknown action:') as error:
            await app.dispatch(action, {}, include_state=False)
        assert error.value.status == 404
    assert events == []
    assert counts == dict.fromkeys(FACTORIES, 0)
