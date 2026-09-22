"""Large chat libraries share a projection snapshot, never cached write authority."""
from pathlib import Path
from types import SimpleNamespace
import uuid

import pytest

from amplifier_foundation.session import SharedSessionStore
from amplifier_web.portability import Portability


@pytest.fixture
def portability(tmp_path, monkeypatch):
    monkeypatch.setattr('amplifier_web.portability.local_host_identity', lambda: {'label': 'Projection fixture'})
    app = SimpleNamespace(data_dir=tmp_path, state={'sessions': []})
    return Portability(app)


def receipt(sid, *, phase='active', generation=1):
    return {'id': str(uuid.uuid4()), 'sessionId': sid, 'phase': phase,
            'generation': generation, 'createdAt': generation,
            'evidence': {'notes': ['retained']}}


def test_empty_receipts_scan_once_for_22915_chat_library(portability, monkeypatch):
    sessions = [{'id': f'history-{index}'} for index in range(22_915)]
    sessions[0]['configurationBusy'] = True
    portability.app.state['sessions'] = sessions
    scans = []
    original_glob = Path.glob

    def tracked_glob(path, pattern, *args, **kwargs):
        if path == portability.node.directory / 'receipts':
            scans.append(pattern)
        return original_glob(path, pattern, *args, **kwargs)

    monkeypatch.setattr(Path, 'glob', tracked_glob)
    portability.sync()

    assert scans == ['*.json']
    assert portability.app.state['portability']['receipts'] == []
    assert all('portability' not in session for session in sessions)
    assert sessions[0]['configurationBusy'] is True


def test_consecutive_snapshots_refresh_and_preserve_independent_values(portability, monkeypatch):
    session = {'id': str(uuid.uuid4())}
    unrelated = {'id': 'unrelated'}
    portability.app.state['sessions'] = [session, unrelated]
    portability.sync()
    assert portability.app.state['portability']['receipts'] == []

    row = receipt(session['id'], phase='preparing')
    portability.node.save(row)
    reads = []
    original_records = portability.node.records

    def tracked_records(sid=None):
        reads.append(sid)
        return original_records(sid)

    monkeypatch.setattr(portability.node, 'records', tracked_records)
    portability.sync()
    assert reads.count(None) == 1
    assert unrelated['id'] not in reads
    assert session['portability']['fenced'] is True
    assert session['configurationBusy'] is True
    assert 'portability' not in unrelated

    global_row = portability.app.state['portability']['receipts'][0]
    session_row = session['portability']['receipts'][0]
    global_row['evidence']['notes'].append('global-only')
    assert session_row['evidence']['notes'] == ['retained']
    session_row['evidence']['notes'].append('session-only')
    assert global_row['evidence']['notes'] == ['retained', 'global-only']
    assert portability.node.get(row['id'])['evidence']['notes'] == ['retained']

    row['phase'] = 'cancelled'
    portability.node.save(row)
    portability.sync()
    assert session['portability']['receipts'][0]['phase'] == 'cancelled'
    assert session['portability']['receipts'][0]['evidence']['notes'] == ['retained']
    assert session['portability']['fenced'] is False
    # Projection does not clear a configuration hold owned by another workflow.
    assert session['configurationBusy'] is True


def test_write_admission_reads_new_generation_without_projection_sync(portability):
    session = {'id': str(uuid.uuid4())}
    portability.app.state['sessions'] = [session]
    first = receipt(session['id'])
    portability.node.save(first)
    portability.sync()
    assert session['portability']['fenced'] is False
    assert portability.write_context(session['id']) == first['id']

    later = receipt(session['id'], phase='preparing', generation=2)
    portability.node.save(later)
    with pytest.raises(ValueError, match='fenced for transfer'):
        portability.write_context(session['id'])
    later['phase'] = 'cancelled'
    portability.node.save(later)
    assert portability.write_context(session['id']) == later['id']
    assert session['portability']['receipts'] == [first]


def test_native_fence_created_after_projection_denies_writes(portability, tmp_path):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    session = {'id': str(uuid.uuid4()), 'workspace': str(workspace)}
    portability.app.state['sessions'] = [session]
    portability.node.save(receipt(session['id']))
    portability.sync()
    assert session['portability']['fenced'] is False

    store = SharedSessionStore(workspace, session['id'])
    held = store.acquire(app='projection-fixture')
    try:
        held.fence_transfer(str(uuid.uuid4()), 'other-host')
    finally:
        held.release()
    with pytest.raises(ValueError, match='fenced for transfer'):
        portability.write_context(session['id'])
