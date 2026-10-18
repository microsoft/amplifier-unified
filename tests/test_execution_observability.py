"""Persisted display-only controls use the same public composition contract."""
from copy import deepcopy
import sqlite3
from types import SimpleNamespace

import pytest
from jsonschema import ValidationError, validate

from amplifier_web.shell_modules import COMPOSITION, ShellModules
from test_shell_modules import service, command, prepare


async def test_metric_preferences_survive_restart_and_new_client_without_usage_changes(service):
    before = deepcopy(service.state['sessions'])
    client = service.shell.client('browser-one')
    composition = deepcopy(client['composition'])
    composition['presentation'].update(executionVisibility={'step': False, 'turn': True, 'conversation': False},
                                     executionMetrics=['costUsd', 'cacheReadTokens', 'voiceSeconds'], conversationStatus=True)
    change = await prepare(service, composition)
    receipt = await command(service, 'shell.changes.apply', clientId='browser-one', expectedRevision=0, changeId=change)
    assert receipt['accepted']
    assert service.state['sessions'] == before
    # Reopening the actual persisted shell store is a host restart boundary.
    path = service.db.execute('PRAGMA database_list').fetchone()[2]
    fresh = sqlite3.connect(path)
    shell = ShellModules(SimpleNamespace(db=fresh,data_dir=service.data_dir,state=service.state))
    for client_id in ('browser-one', 'new-browser'):
        p = shell.client(client_id)['composition']['presentation']
        assert p['executionVisibility'] == {'step': False, 'turn': True, 'conversation': False}
        assert p['executionMetrics'] == ['costUsd', 'cacheReadTokens', 'voiceSeconds']
        assert p['conversationStatus'] is True
    fresh.close()


def test_all_ten_execution_metrics_can_be_selected():
    metrics = COMPOSITION['properties']['presentation']['properties']['executionMetrics']['items']['enum']
    assert len(metrics) == 10 and 'voiceSeconds' in metrics
    validate({'executionMetrics': metrics}, COMPOSITION['properties']['presentation'])


@pytest.mark.parametrize('patch', [
    {'executionVisibility': {'secret': True}}, {'executionVisibility': {'step': 'yes'}},
    {'executionMetrics': ['reasoningText']}, {'executionMetrics': ['costUsd', 'costUsd']},
    {'conversationStatus': 'yes'},
])
def test_schema_rejects_unknown_metrics_or_non_boolean_visibility(patch):
    with pytest.raises(ValidationError):
        validate(patch, COMPOSITION['properties']['presentation'])
