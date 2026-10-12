"""Agent declarations sealed by exact native checkpoint and Host settlement."""
import json
from .grants import digest


def definition(schema, string):
    return {'description': 'Reply to an exact delivered peer request in this active root generation. Acknowledgement, defer, decline and partial/failed results are explicit. Success remains staged until this generation finishes with a saved native answer. References are agent assertions, not independently verified correctness. Does not start another turn.',
            'schema': schema({'sessionId': string(512), 'requestId': string(200),
                'kind': {'enum': ['result', 'ack', 'defer', 'decline']},
                'outcome': {'enum': ['success', 'partial', 'failed', 'unverified']},
                'text': string(12000), 'references': {'type': 'array', 'maxItems': 16, 'uniqueItems': True, 'items': string(2048)}},
                ['sessionId', 'requestId', 'kind', 'outcome', 'text'])}


async def reply(peer, params, source):
    owner, args = peer.owner, params['args']
    if params.get('origin') != 'agent' or not params.get('resultsEnabled'):
        raise ValueError('A live authenticated recipient agent with native result proof is required')
    command = params.get('commandId')
    if not isinstance(command, str) or not 1 <= len(command) <= 200 or not args['text'].strip():
        raise ValueError('A stable reply identity and nonempty result are required')
    signature = digest({key: params.get(key) for key in ('operation', 'args', 'origin', 'callerSession', 'actorId')})
    async with owner.lock:
        row = peer.read(args['requestId'])
        if row['target']['sessionId'] != source['sessionId'] or row['mode'] not in {'queue', 'steer'}:
            raise ValueError('Only the exact recipient can reply to a response request')
        previous = owner.receipt(command)
        if previous:
            if previous['requestHash'] != signature:
                raise ValueError('Reply command identity conflicts')
            return {'receipt': previous, 'replayed': False}
        if row['status'] not in {'submitting', 'accepted', 'applied'} or row.get('response'):
            raise ValueError('This request is not awaiting an original active reply')
        await peer.guard_scope(row)
        binding = await owner.host('readActivePeerInput', {'session': source['sessionId'], 'inputId': row['inputId'], 'actorId': params['actorId']})
        if (binding.get('sessionId') != source['sessionId'] or binding.get('nativeSessionId') != source['nativeSessionId']
                or binding.get('inputId') != row['inputId'] or binding.get('grantId') != row['grantId']
                or binding.get('interruptionRevision') != row['interruptionRevision']
                or not binding.get('generationId') or not binding.get('activeTurnId')
                or row['mode'] == 'queue' and binding['activeTurnId'] != row['inputId']
                or row['mode'] == 'steer' and (binding['generationId'] != row['targetGenerationId'] or binding['activeTurnId'] != row['activeTurnId'])):
            raise ValueError('The reply does not belong to this delivered peer generation')
        response = {key: args[key] for key in ('kind', 'outcome', 'text', 'references') if key in args}
        response.update(status='staged', qualified=False, binding=binding)
        row['response'] = response
        receipt = {'commandId': command, 'requestHash': signature, 'operation': 'coordination.reply',
                   'requestId': row['commandId'], 'target': row['target'], 'status': 'staged', 'qualified': False}
        for identity, sig, value in [(command, signature, receipt), (row['commandId'], row['requestHash'], row)]:
            owner.db.execute('INSERT OR REPLACE INTO commands VALUES(?,?,?)', (identity, sig, json.dumps(value)))
        owner.db.commit()
    return {'receipt': receipt, 'response': response, 'replayed': False}


async def seal(peer, row, event):
    response = row.get('response')
    if not response or response['status'] != 'staged':
        return
    binding = response['binding']
    response.update(status='unqualified', qualified=False, detail='No exact saved terminal answer was confirmed')
    if event['status'] != 'completed' or binding['activeTurnId'] != event['commandId']:
        return
    proofs = [proof for proof in event.get('peerTerminals', []) if isinstance(proof, dict)
              and proof.get('version') == 1 and proof.get('rootSessionId') == binding['nativeSessionId']
              and proof.get('generationId') == binding['generationId']
              and row['inputId'] in proof.get('inputIds', [])
              and proof.get('messageId') and proof.get('sourceRevision') and proof.get('textDigest')]
    if len(proofs) != 1:
        return
    try:
        await peer.guard_scope(row)
    except ValueError:
        response['detail'] = 'Peer authority changed before terminal settlement'
        return
    response.update(status='sealed', terminal=proofs[0], qualified=response['kind'] == 'result' and response['outcome'] == 'success',
                    detail='Agent declaration with exact saved terminal evidence; correctness is not independently verified')
