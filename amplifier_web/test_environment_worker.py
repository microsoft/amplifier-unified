"""One bounded DTU library call in its managed core interpreter.

Invoked by the host with private stdin, never registered as a generic tool/MCP
server. Only fixed profiles and owner-derived paths cross this boundary.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys


def serialize(value):
    return value.model_dump(mode='json')


def profile(image):
    return {
        'name': 'unified-test',
        'services': {'twin': {
            'image': image, 'command': ['sleep', 'infinity'],
            'read_only': True, 'cap_drop': ['ALL'],
            'security_opt': ['no-new-privileges:true'],
            'pids_limit': 128, 'mem_limit': '1g', 'cpus': 2,
            'healthcheck': {'test': ['CMD', 'true'], 'interval': '2s'},
            'tmpfs': ['/tmp:rw,noexec,nosuid,size=67108864'],
            'volumes': ['workspace:/workspace', 'artifacts:/artifacts'],
            'networks': ['default'], 'working_dir': '/workspace',
            # Root only inside this container, with no capabilities or host mounts.
            # Docker copy initializes the fresh owned volumes before commands run.
            'user': '0:0',
        }},
        'volumes': {'workspace': {}, 'artifacts': {}},
        'networks': {'default': {'internal': True}},
        'x-dtu': {'twin_machine': 'twin'},
    }


def execute_call(payload):
    from digital_twin_universe import lib
    from digital_twin_universe.schemas import BackendConfig, DigitalTwinUniverseError
    root = Path(payload['root'])
    config = BackendConfig(state_root=root / 'backend', docker_host=payload['dockerHost'])
    action = payload['action']
    identity = payload.get('universeId')
    snapshot = None
    try:
        if action == 'prepare':
            # Source snapshot is immutable and retained even when launch fails.
            snapshot = serialize(lib.export_repository_snapshot(Path(payload['source']), root / 'source.tar'))
            (root / 'snapshot.json').write_text(json.dumps(snapshot))
            return {'ok': True, 'universeId': lib.launch_id(payload['operationId'], config=config), 'snapshot': snapshot}
        if action == 'start':
            compose = root / 'compose.json'
            compose.write_text(json.dumps(profile(payload['image'])))
            universe = lib.launch(compose, operation_id=payload['operationId'], timeout_seconds=600, config=config)
            identity = universe.id
            lib.push_files(identity, root / 'source.tar', '/artifacts/.source.tar', config=config)
            unpack = lib.execute(identity, 'python -c "import tarfile; tarfile.open(\'/artifacts/.source.tar\').extractall(\'/workspace\', filter=\'data\')" && rm /artifacts/.source.tar',
                workdir='/', timeout_seconds=60, max_output_bytes=32768, config=config)
            if unpack.exit_code != 0:
                return {'ok': False, 'universeId': identity, 'snapshot': snapshot, 'status': 'unknown',
                        'message': 'The environment launched but source preparation did not complete. Inspect it; launch was not replayed.', 'result': serialize(unpack)}
            return {'ok': True, 'universeId': identity,
                    'status': universe.state, 'services': [serialize(item) for item in universe.services]}
        if not identity:
            # One resource has its own backend root. Exact launch identity is the
            # recovery key when the host lost the original helper response.
            identity = lib.launch_id(payload['operationId'], config=config)
        if action == 'read':
            universe = lib.status(identity, config=config)
            return {'ok': True, 'universeId': identity, 'status': universe.state,
                    'services': [serialize(item) for item in universe.services]}
        if action == 'execute':
            result = lib.execute(identity, payload['command'], workdir='/workspace',
                timeout_seconds=payload['timeoutSeconds'], max_output_bytes=32768, config=config)
            return {'ok': True, 'universeId': identity, 'result': serialize(result)}
        if action == 'stop':
            result = lib.stop(identity, config=config)
            return {'ok': result.complete, 'universeId': identity,
                    'status': 'stopped' if result.complete else 'unknown', 'result': serialize(result),
                    'message': 'Containers stopped; source and artifacts are retained.' if result.complete else 'Stop was requested but not confirmed. Inspect current state.'}
        if action == 'remove':
            universe = lib.status(identity, config=config)
            if universe.state != 'stopped' or any(item.state == 'running' for item in universe.services):
                return {'ok': False, 'universeId': identity, 'status': 'unknown', 'message': 'Removal requires observed stopped containers. No data was removed.'}
            destination = root / ('retained-' + hashlib.sha256(payload['requestId'].encode()).hexdigest())
            destination.mkdir(mode=0o700)
            archives = [serialize(lib.export_files(identity, source, destination / name, config=config))
                        for source, name in [('/workspace', 'workspace.tar'), ('/artifacts', 'artifacts.tar')]]
            (destination / 'manifest.json').write_text(json.dumps(archives))
            result = lib.destroy(identity, config=config)
            return {'ok': result.complete, 'universeId': identity,
                    'status': 'removed' if result.complete else 'unknown', 'result': serialize(result),
                    'archives': archives, 'message': 'Source and artifacts were retained before removal.' if result.complete else 'Artifacts were retained; teardown is not confirmed.'}
        raise ValueError('Unsupported managed test environment action.')
    except DigitalTwinUniverseError as exc:
        return {'ok': False, 'status': 'unknown', 'universeId': identity or exc.universe_id,
                'snapshot': snapshot, 'code': exc.code,
                'message': 'The backend did not confirm this operation. A command may still be running; inspect the retained environment without replaying the request.'}


def main():
    try:
        payload = json.loads(sys.stdin.buffer.read(65537))
        value = execute_call(payload)
    except Exception:
        value = {'ok': False, 'status': 'unknown',
                 'message': 'The managed backend failed before returning a confirmed result. Inspect retained state; this request will not replay.'}
    sys.stdout.write(json.dumps(value))


if __name__ == '__main__':
    main()
