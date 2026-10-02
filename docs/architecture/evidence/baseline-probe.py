"""Disposable source-level probe; no installed service or model calls."""
import json
import statistics
import time
from pathlib import Path

import pytest
from test_automatic_history import app_factory
from test_browser_state import catalog


@pytest.mark.parametrize('count,clients', [(100,1),(5000,1),(22500,1),(22500,8)])
async def test_cost_shape(app_factory, count, clients):
    app = app_factory()
    for queue in list(app.queues):
        app.unsubscribe(queue)
    rows = catalog(app, count=count, workers=0, workspaces=min(4000,count//5))
    for i in range(clients):
        rows[i].update(historyManaged=False,historyLoaded=True)
        rows[i]['messages'] = [{'id':f'm-{i}', 'role':'assistant','text':'Fixed visible body.'}]
        record=app.clients.attach(f'probe-{i}')
        record.update(selectedSessionId=rows[i]['id'],selectedWorkspaceId=rows[i]['workspaceId'])
        with app.clients.bind(f'probe-{i}'):
            app.subscribe()
    app._publish()
    app.cold_display.last_sweep = time.monotonic()
    result = {'saved_chats':count,'clients':clients,'source':'e9a3a2fa9a6f2291e451fb95a0202b0a0b9156e6'}
    for name in ['scoped_detail','global_publication']:
        durations=[]
        for repeat in range(3):
            rows[0]['streaming']=str(repeat)
            start=time.perf_counter()
            if name=='scoped_detail':
                app._publish(session_ids={rows[0]['id']},detail_only=True,record_only=True)
            else:
                app._state['management']={'phase':'ready','operation':'probe','sequence':repeat}
                app._publish()
            durations.append(round((time.perf_counter()-start)*1000,2))
        result[name]={'ms':durations,'median_ms':statistics.median(durations)}
    for name in ['private_draft_action','agent_scalar_read']:
        durations=[]
        for repeat in range(3):
            start=time.perf_counter()
            with app.clients.bind('probe-0'):
                if name=='private_draft_action':
                    await app.dispatch('view.update',{'sessionId':rows[0]['id'],'patch':{'draft':'probe-'+str(repeat)}},include_state=False)
                else:
                    await app.app_bridge('get_state',{'path':'/revision'},rows[0]['id'])
            durations.append(round((time.perf_counter()-start)*1000,2))
        result[name]={'ms':durations,'median_ms':statistics.median(durations)}
    print('ARCHITECTURE_PROBE '+json.dumps(result),flush=True)
    with Path('/tmp/unified-architecture-review-e9a3a2fa/probe-results.jsonl').open('a') as f:
        f.write(json.dumps(result)+'\n')
