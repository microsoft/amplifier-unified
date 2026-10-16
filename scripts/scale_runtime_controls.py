#!/usr/bin/env python3
"""Authenticated negative and single-worker positive controls, private DTU only."""
import http.client
import json
from pathlib import Path
import socket
import time
import uuid

BASE = Path('/var/lib/unified-scale-runtime-20261015')


class UnixHTTP(http.client.HTTPConnection):
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(15)
        self.sock.connect(str(BASE / 'run/web.sock'))


def main():
    ready = json.loads((BASE / 'run/ready.json').read_text())
    token = (BASE / 'writable/app/config/auth/control-token').read_text().strip()
    client = 'combined-controls-' + uuid.uuid4().hex
    headers = {'Authorization': 'Bearer ' + token, 'X-Amplifier-Client': client,
               'X-Amplifier-State-Transport': 'delta-v1', 'Content-Type': 'application/json'}
    def request(method, path, body=None):
        conn = UnixHTTP('127.0.0.1', 8943)
        try:
            conn.request(method, path, json.dumps(body) if body is not None else None, headers)
            response = conn.getresponse()
            raw = response.read(8 * 1024 * 1024 + 1)
            if len(raw) > 8 * 1024 * 1024: raise RuntimeError('Oversized control response')
            if response.getheader('Content-Type', '').startswith('application/json'):
                return response.status, json.loads(raw)
            return response.status, {'responseType': 'non-json', 'body': raw.decode(errors='replace')[:512]}
        finally:
            conn.close()
    def metrics():
        status, value = request('GET', '/fixture/runtime')
        if status != 200: raise RuntimeError('Synthetic metrics unavailable')
        return value
    status, _ = request('POST', '/api/clients/attach',
        {'clientId': client, 'kind': 'web', 'protocolVersion': 1, 'includeState': False})
    if status != 200: raise RuntimeError('Control client attach failed')
    before = metrics()
    if before['workers'] or before['controls']['realNegativeControls'] != 7:
        raise RuntimeError('Required startup negative controls missing')
    copied = json.loads(Path('/var/lib/unified-scale-replica-20261015/samples.json').read_text())[0]['id']
    refused = []
    for action, args in [('conversation.send', {'sessionId': copied, 'text': 'MUST NEVER RUN'}),
                         ('session.rename', {'id': copied, 'title': 'MUST NEVER CHANGE'}),
                         ('session.create', {'title': 'UNLISTED CHILD'})]:
        status, _ = request('POST', '/api/actions', {'id': uuid.uuid4().hex, 'action': action, 'args': args})
        if status not in {400, 403, 409}: raise RuntimeError('HTTP effect negative was accepted')
        refused.append({'action': action, 'status': status})
    if metrics()['workers']: raise RuntimeError('Negative HTTP effects started a worker')
    sid = ready['ids'][0]
    status, result = request('POST', '/api/actions', {'id': uuid.uuid4().hex,
        'action': 'conversation.send', 'args': {'sessionId': sid, 'text': '[BENCH:0:0:0] Synthetic single-owner qualification'}})
    if status != 200 or result.get('accepted') is not True:
        raise RuntimeError('Synthetic positive admission was refused: ' + str(status) + ' ' + str(result))
    deadline = time.monotonic() + 60
    while True:
        current = metrics()
        if any(row['id'] != sid for row in current['workers']):
            raise RuntimeError('Worker escaped exact positive owner')
        selected = next(row for row in current['sessions'] if row['id'] == sid)
        if selected['status'] == 'error': raise RuntimeError('Synthetic worker failed before streaming: ' + str(selected.get('error')))
        if selected['streamingBytes'] > 0: break
        if time.monotonic() > deadline: raise RuntimeError('Synthetic worker did not stream within bounded qualification')
        time.sleep(.2)
    status, _ = request('POST', '/api/actions', {'id': uuid.uuid4().hex,
        'action': 'conversation.stop', 'args': {'sessionId': sid}})
    if status != 200: raise RuntimeError('Synthetic owner stop was refused')
    deadline = time.monotonic() + 20
    while metrics()['workers']:
        if time.monotonic() > deadline: raise RuntimeError('Synthetic child did not exit after Stop')
        time.sleep(.2)
    receipt = {'passed': True, 'source': ready['source'], 'controls': ready['controls'],
               'httpNegatives': refused, 'positiveRealWorkerStreamed': True,
               'positiveOwnerStopped': True, 'remainingWorkers': 0,
               'qualificationBypassed': True, 'provider': 'local deterministic; no external credentials',
               'timingMeasurement': False}
    (BASE / 'run/control-proof.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps(receipt))


if __name__ == '__main__':
    main()
