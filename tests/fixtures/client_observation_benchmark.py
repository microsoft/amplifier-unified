"""Synthetic observation construction budget; run inside the project's DTU."""
import json
from pathlib import Path
import sys
import time
import tracemalloc
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from amplifier_web.client_observations import Observations

rows = [{'id': str(i), 'title': 'Synthetic chat', 'status': 'idle',
         'completion': {'id': str(i), 'at': i}, 'approvals': [{'id': str(i)}]} for i in range(10000)]
service = SimpleNamespace(_state={'sessions': rows, 'pinnedSessionIds': [r['id'] for r in rows]},
                          instance_id='synthetic-benchmark', projections=SimpleNamespace(observation_generation=1))
observations = Observations(service)
tracemalloc.start()
started = time.perf_counter()
latencies, count_requests = [], 0
while True:
    before = time.perf_counter()
    value = observations.activity()
    latencies.append(time.perf_counter() - before)
    count_requests += 1
    if value['status'] == 'ready':
        break
counts_seconds = time.perf_counter() - started
results = {}
for collection in ('sessions', 'attention'):
    cursor, total, requests = None, 0, 0
    while True:
        before = time.perf_counter()
        page = observations.page(collection, {'limit': '200', 'cursor': cursor})
        latencies.append(time.perf_counter() - before)
        total += len(page['items'])
        requests += 1
        cursor = page['nextCursor']
        if cursor is None:
            break
    results[collection] = {'items': total, 'requests': requests}
_, peak = tracemalloc.get_traced_memory()
tracemalloc.stop()
assert results['sessions']['items'] == 10000 and results['attention']['items'] == 20000
assert value['attentionCount'] == 20000 and value['requiresActionCount'] == 10000
print(json.dumps({'catalogSessions': 10000, 'countRequests': count_requests,
                  'countsSeconds': counts_seconds, 'maxRequestSeconds': max(latencies),
                  'incrementalPeakBytes': peak, 'tracemallocEnabled': True, 'collections': results}, indent=2))
