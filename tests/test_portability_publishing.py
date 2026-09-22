"""Integration boundary: retained publishing controls stay on their source."""
from pathlib import Path

import pytest

from amplifier_web.service import AppError
from test_portability import setup_hosts, host_env


async def test_retained_stopped_publication_blocks_transfer_without_changing_source(tmp_path, monkeypatch):
    Publishing = pytest.importorskip('amplifier_web.publishing').Publishing
    app, target, source, destination, root, destrepo, sid, *_ = await setup_hosts(tmp_path, monkeypatch)
    host_env(monkeypatch, source)
    publishing = getattr(app, 'publishing', None) or Publishing(app)
    app.publishing = publishing
    try:
        dist = root / 'dist'; dist.mkdir()
        (dist / 'index.html').write_text('<h1>Retained source publication</h1>')
        release = publishing.store.build(dist, site_id='portable-site', session_id=sid, request_id='build')
        publishing.store.review(release['id'], note='Reviewed synthetic page', session_id=sid, request_id='review')
        publishing.store.deploy(release['id'], site_id='portable-site', expected_revision=0, session_id=sid, request_id='deploy')
        current = publishing.store.status('portable-site', sid)
        publishing.store.stop(site_id='portable-site', expected_revision=current['revision'], session_id=sid, request_id='stop')
        before = publishing.snapshot(sid)
        original = app._session(sid).copy()
        inspected = (await app.dispatch('worktree.inspect', {'sessionId': sid}))['result']
        with pytest.raises(AppError, match='retained publishing state'):
            await app.dispatch('portability.export', {'sessionId': sid,
                'destination': target.portability.node.identity['id'],
                'sourceRevision': inspected['repository']['sourceRevision'],
                'expectedExecutionRevision': 0, 'mode': 'carry_dirty', 'reviewedContent': True})
        assert publishing.snapshot(sid) == before
        assert app._session(sid)['messages'] == original['messages']
        assert app._session(sid).get('configurationBusy') == original.get('configurationBusy')
        assert app.portability.node.records(sid) == []
        # Audit/management remains available, including retained immutable bytes.
        assert publishing.store.status('portable-site', sid)['releaseId'] == release['id']
        assert (dist / 'index.html').read_text() == '<h1>Retained source publication</h1>'
    finally:
        await publishing.close()
        await app.close(); await target.close()
