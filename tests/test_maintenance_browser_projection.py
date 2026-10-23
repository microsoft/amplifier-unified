"""Canonical maintenance summary remains available across browser surfaces."""
import copy
import json
from amplifier_web.service import AppService


async def test_browser_projection_keeps_canonical_summary_without_private_root_proofs(tmp_path):
    app = AppService(tmp_path / 'app', None, workspace=tmp_path)
    try:
        app.clients.attach('maintenance-browser')
        expected = copy.deepcopy(app.state['hostMaintenance'])
        for panel, page in [('activity', None), ('settings', 'updates'), (None, None)]:
            with app.clients.bind('maintenance-browser'):
                await app.dispatch('view.update', {'patch': {
                    'panel': panel, 'settingsExpanded': [page] if page else []}})
                snapshot = app.browser_state()
            assert snapshot['hostMaintenance'] == expected
            assert 'roots' not in snapshot['hostMaintenance']
            assert 'checkpointId' not in json.dumps(snapshot['hostMaintenance'])
    finally:
        await app.close()
