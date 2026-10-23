"""Output actions return exact domain receipts without an unrelated app overview."""
import hashlib
import json

import pytest

from amplifier_web.service import AppError, AppService
from test_service import Runtime


@pytest.fixture
async def app(tmp_path):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    service = AppService(tmp_path / 'app', workspace=workspace, runtime=Runtime())
    await service.dispatch('session.create', {'title': 'Receipt owner'})
    yield service
    await service.close()


async def test_agent_attach_returns_exact_receipt_without_building_overview(app, monkeypatch):
    from pathlib import Path

    sid = app._session()['id']
    content = b'Synthetic retained evidence\n'
    (Path(app._session()['workspace']) / 'evidence.txt').write_bytes(content)

    def unexpected(*args, **kwargs):
        raise AssertionError('Output receipt must not request the app overview')

    monkeypatch.setattr(app, 'browser_state', unexpected)
    monkeypatch.setattr('amplifier_web.agent_state.read_state', unexpected)
    arguments = {'action': 'outputs.attach', 'id': 'attach-evidence', 'args': {
        'kind': 'file', 'title': 'Evidence', 'path': 'evidence.txt',
        'expectedSha256': hashlib.sha256(content).hexdigest(),
    }}
    receipt = await app.app_bridge('dispatch', arguments, sid)
    row = receipt['result']
    assert receipt['accepted'] is True
    assert row['sha256'] == hashlib.sha256(content).hexdigest()
    assert row['sessionId'] == sid and row['bytes'] == len(content)
    assert app.outputs.content(row) == content
    assert set(receipt['state']) == {'revision', 'sessionId', '_stateAccess'}
    assert len(json.dumps(receipt)) < 3_000
    duplicate = await app.app_bridge('dispatch', arguments, sid)
    assert duplicate['result']['id'] == row['id']
    assert duplicate['result']['duplicate'] is True
    listed = await app.app_bridge('dispatch', {'action': 'outputs.list', 'args': {'limit': 1}}, sid)
    assert len(listed['result']['items']) == 1
    assert listed['result']['items'][0]['id'] == row['id']
    read = await app.app_bridge('dispatch', {'action': 'outputs.read', 'args': {'id': row['id']}}, sid)
    assert read['result']['text'] == content.decode()
    assert not app.runtime.sent


async def test_compact_output_receipt_preserves_scope_and_file_refusals(app, tmp_path):
    sid = app._session()['id']
    outside = tmp_path / 'outside.txt'
    outside.write_text('Outside execution root')
    with pytest.raises(AppError, match='inside'):
        await app.app_bridge('dispatch', {'action': 'outputs.attach', 'id': 'outside', 'args': {
            'kind': 'file', 'title': 'Outside', 'path': str(outside),
        }}, sid)
    await app.dispatch('session.create', {'title': 'Other owner'})
    other = app._session()['id']
    with pytest.raises(AppError, match='calling conversation'):
        await app.app_bridge('dispatch', {'action': 'outputs.list', 'args': {'sessionId': sid}}, other)
    assert app.outputs.store.list(sid)['items'] == []


async def test_ui_output_dispatch_keeps_its_existing_domain_receipt(app):
    sid = app._session()['id']
    result = await app.dispatch('outputs.write', {
        'sessionId': sid, 'title': 'Memo', 'content': 'Retained memo', 'variant': 'document',
    }, command_id='ui-write')
    assert result['accepted'] is True
    assert set(result) == {'accepted', 'result'}
    assert app.state['selectedSessionId'] == sid
    assert result['result']['id']
