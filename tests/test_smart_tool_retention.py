"""Retention accounting stays bounded and never reads or replays tool bodies."""
import json

import pytest

from amplifier_web import storage_cleanup
from test_smart_tools import manager


def insert(manager, identity, **fields):
    value = {'id': identity, 'status': 'completed', **fields}
    manager.service.db.execute('INSERT INTO smart_tool_operations VALUES(?,?)', (identity, json.dumps(value)))
    manager.service.db.commit()


async def test_accounting_reports_receipts_and_shared_body_references_without_hydration(manager, monkeypatch):
    monkeypatch.setattr(manager, 'operation', lambda *_: pytest.fail('Full operation read'))
    from amplifier_web import state_storage
    monkeypatch.setattr(state_storage, 'resource', lambda *_: pytest.fail('Resource hydration'))
    insert(manager, 'one', result={'$resource': 'same-body', 'bytes': 12345}, arguments={'value': 'hi'})
    insert(manager, 'two', result={'$resource': 'same-body', 'bytes': 12345})
    manager.service.smart_tools = manager
    before = list(manager.service.db.execute('SELECT * FROM smart_tool_operations'))
    page = (await storage_cleanup.dispatch(manager.service, 'storage.cleanup.evidence', {'owner': 'smartTools'}, 'ui', None))['result']
    assert len(page['items']) == 2 and page['unaccountedCount'] == 0
    assert page['payloadBytes'] == 24690 + len('{"value":"hi"}'.encode())
    assert page['receiptBytes'] > 0 and page['recordBytes'] > page['receiptBytes']
    assert 'not allocated disk' in page['note'] and 'Shared references' in page['note']
    assert before == list(manager.service.db.execute('SELECT * FROM smart_tool_operations'))
    assert not manager.service.published


async def test_accounting_pages_legacy_large_and_incomplete_records_without_false_zero(manager):
    insert(manager, 'legacy-large', result='not projected' * 30000)
    insert(manager, 'missing-size', result={'$resource': 'unknown-size'})
    for index in range(32):
        insert(manager, str(index), resultExpired=True)
    first = manager.retention_accounting()
    assert len(first['items']) == 32 and first['payloadBytes'] == 0 and first['unaccountedCount'] == 0
    insert(manager, 'newest', result={'later': True})
    second = manager.retention_accounting(first['nextBefore'])
    assert [item['id'] for item in second['items']] == ['missing-size', 'legacy-large']
    assert second['nextBefore'] is None and second['unaccountedCount'] == 2
    assert all(item['resultBytes'] is None for item in second['items'])
    assert second['items'][1]['receiptBytes'] is None
    assert second['items'][1]['recordBytes'] > 262144
    assert len(json.dumps(second)) < 2000


@pytest.mark.parametrize('before', [True, 0, -1, 1.5, '1', 2**53])
async def test_accounting_rejects_invalid_cursors(manager, before):
    with pytest.raises(ValueError, match='cursor'):
        manager.retention_accounting(before)
