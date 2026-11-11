"""Synthetic pipe peer for the host's real startup/admission adapter.

No Amplifier runtime, provider or tool executes here. The host owns all guards,
projection and coordination.admit decisions; this fixture only speaks the worker
pipe protocol and exposes deterministic readiness/acknowledgement checkpoints.
"""
import json
import sys
from pathlib import Path


def publish(value):
    print(json.dumps(value), flush=True)


def bridge(identity, operation, args):
    publish({'op': 'bridge', 'id': identity, 'operation': operation, 'args': args})
    reply = json.loads(sys.stdin.readline())
    if reply['op'] == 'stop':
        raise SystemExit(0)
    assert reply['op'] == 'bridge.result' and reply['id'] == identity, reply
    assert 'error' not in reply, reply
    return reply['result']


def main():
    log = Path(sys.argv[1])
    for line in sys.stdin:
        command = json.loads(line)
        with log.open('a') as stream:
            stream.write(json.dumps(command) + '\n')
        if command['op'] == 'start':
            bridge('before-ready', 'fixture.before_ready', {})
            publish({'type': 'runtime.ready', 'report': {}})
        elif command['op'] == 'control':
            operation, args = command['operation'], command['arguments']
            if operation == 'coordination.submit':
                admitted = bridge('admit', 'coordination.admit', args)
                bridge('before-ack', 'fixture.before_ack', {})
                result = {'accepted': admitted['admitted'], 'inputId': args['inputId'],
                          'completed': False, 'reason': admitted.get('reason')}
            elif operation == 'fixture.generation':
                publish({'type': 'generation.started', 'generation_id': 'fresh-generation',
                         'input_ids': ['resume-member']})
                result = {'emitted': True}
            else:
                raise AssertionError(command)
            publish({'op': 'reply', 'id': command['id'], 'result': result})
        elif command['op'] == 'stop':
            break
        else:
            raise AssertionError(command)


if __name__ == '__main__':
    main()