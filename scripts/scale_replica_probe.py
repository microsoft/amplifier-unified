#!/usr/bin/env python3
"""Bounded private-replica HTTP proof; retain scalars, never message bodies."""
import http.client
import json
from pathlib import Path
import socket
import time
from urllib.parse import urlencode
import uuid

BASE = Path('/var/lib/unified-scale-replica-20261015')


class UnixHTTP(http.client.HTTPConnection):
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(15)
        self.sock.connect(str(BASE / 'run/web.sock'))


def main():
    token = (BASE / 'seed/app/config/auth/control-token').read_text().strip()
    client = 'private-scale-' + uuid.uuid4().hex
    headers = {'Authorization': 'Bearer ' + token, 'X-Amplifier-Client': client,
               'X-Amplifier-State-Transport': 'delta-v1', 'Content-Type': 'application/json'}
    def request(method, path, body=None):
        conn = UnixHTTP('127.0.0.1', 8942)
        start = time.monotonic()
        try:
            conn.request(method, path, json.dumps(body) if body is not None else None, headers)
            response = conn.getresponse()
            raw = response.read(8 * 1024 * 1024 + 1)
            if len(raw) > 8 * 1024 * 1024:
                raise ValueError('Probe response exceeds bounded allowance')
            return response.status, json.loads(raw), (time.monotonic() - start) * 1000, len(raw)
        finally:
            conn.close()
    status, _, elapsed, size = request('POST', '/api/clients/attach',
        {'clientId': client, 'kind': 'web', 'protocolVersion': 1, 'includeState': False})
    if status != 200:
        raise RuntimeError('Replica client attachment failed')
    results = {'clientAttachMs': elapsed, 'clientAttachBytes': size,
               'eventsComplete': False, 'backgroundCaptureActive': True, 'samples': []}
    for sample in json.loads((BASE / 'samples.json').read_text()):
        sid = sample['id']
        status, response, elapsed, size = request('POST', '/api/actions',
            {'id': uuid.uuid4().hex, 'action': 'session.select', 'args': {'id': sid}})
        row = {'label': sample['label'], 'sessionId': sid, 'selectionStatus': status,
               'selectionMs': elapsed, 'selectionBytes': size}
        if status == 200:
            status, page, elapsed, size = request('GET', '/api/conversation/detail?' + urlencode(
                {'sessionId': sid, 'part': 'messages', 'limit': 100}))
            row.update(pageStatus=status, pageMs=elapsed, pageBytes=size,
                       messages=len(page.get('items', [])), mode=page.get('mode'),
                       hasEarlier=page.get('hasEarlier'),
                       coverageKind=page.get('coverage', {}).get('kind'))
        results['samples'].append(row)
    status, _, _, _ = request('POST', '/api/actions', {'id': uuid.uuid4().hex,
        'action': 'conversation.send', 'args': {'sessionId': results['samples'][0]['sessionId'],
        'text': 'SYNTHETIC-BLOCKED-EXECUTION-PROBE'}})
    results['executionMutationRefused'] = status in {400, 403}
    if not results['executionMutationRefused']:
        raise RuntimeError('Replica execution guard failed')
    (BASE / 'initial-page-proof.json').write_text(json.dumps(results, indent=2) + '\n')
    print(json.dumps(results))


if __name__ == '__main__':
    main()
