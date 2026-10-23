"""Read-only native isolation evidence for an exact qualification owner."""
from pathlib import Path

from . import test_environment_profiles as profiles


def inspect(config, home, row):
    expected = profiles.resources(row['profileId'], row['resourceRequest'])
    payload = {**config, 'rootOwner': row['sessionId'], 'operationId': row['launchOperationId'],
               'universeId': row['universeId']}
    if config.get('remoteRunner'):
        from .task_environments import invoke
        result = invoke(config, home, row, 'isolation', {})
        proof = result.get('isolation')
        if (result.get('ok') is not True or not isinstance(proof, dict)
                or proof.get('owner') != row['launchOperationId']):
            raise ValueError('The target could not confirm this check’s isolation.')
        return proof
    if config.get('backend') == 'incus':
        from . import incus_environment_worker as worker
        settings = config['incus']
        env = worker.environment(settings, Path(home))
        _, instance = worker.observe(settings, env, row['universeId'], payload, confinement=True)
        if not instance:
            raise ValueError('The exact owned instance is not observable.')
        values = instance.get('expanded_config', {})
        wanted = {'limits.cpu': str(expected['cpus']), 'limits.memory': str(expected['memoryMiB']) + 'MiB',
                  'limits.processes': str(expected['pids'])}
        if any(values.get(name) != value for name, value in wanted.items()):
            raise ValueError('The configured instance resource limits were not applied.')
        return {'backend': 'incus', 'owner': row['launchOperationId'], 'offline': True,
                'unprivileged': True, 'hostMounts': False, 'limits': wanted}
    from . import test_environment_worker as worker
    daemon = worker.docker_json(payload, 'info', '--format', '{{json .}}')
    if daemon['ID'] != row['backendIdentity']['daemon']:
        raise ValueError('The qualification daemon changed.')
    containers = worker.docker_json(payload, 'container', 'ls', '-a', '--filter',
                                   'label=com.docker.compose.project=' + row['universeId'], '--format', '{{json .}}')
    # Docker's format=json yields JSON lines. Query only the known service name,
    # then verify all ownership labels before reading its dependent resources.
    if not isinstance(containers, list):
        containers = [containers]
    if len(containers) != 1:
        raise ValueError('Qualification requires exactly one owned test service.')
    container = worker.docker_json(payload, 'inspect', containers[0]['ID'])[0]
    labels = container.get('Config', {}).get('Labels', {})
    owner = {'io.amplifier.root-owner': row['sessionId'], 'io.amplifier.operation-id': row['launchOperationId'],
             'com.docker.compose.project': row['universeId']}
    if any(labels.get(key) != value for key, value in owner.items()):
        raise ValueError('The inspected service belongs to another owner.')
    settings = container['HostConfig']
    if (settings.get('Privileged') or settings.get('ReadonlyRootfs') is not True
            or set(settings.get('CapDrop') or []) != {'ALL'}
            or {value.removeprefix('CAP_') for value in settings.get('CapAdd') or []} != {'CHOWN', 'SETUID', 'SETGID', 'KILL'}
            or not any(value in {'no-new-privileges', 'no-new-privileges:true'} for value in settings.get('SecurityOpt', []))
            or settings.get('Devices') or settings.get('DeviceRequests')
            or settings.get('PidMode') == 'host' or settings.get('IpcMode') == 'host'
            or settings.get('NetworkMode') == 'host'
            or settings.get('Memory') != expected['memoryMiB'] * 1024**2
            or settings.get('NanoCpus') != int(expected['cpus'] * 10**9)
            or settings.get('PidsLimit') != expected['pids']
            or settings.get('ShmSize') != expected['shmMiB'] * 1024**2):
        raise ValueError('The configured container isolation or limits were not applied.')
    mounts = container.get('Mounts', [])
    if {item.get('Destination') for item in mounts} != {'/workspace', '/artifacts', '/var/lib/unified-jobs'} or len(mounts) != 3:
        raise ValueError('Unexpected qualification mounts.')
    for mount in mounts:
        if mount.get('Type') != 'volume':
            raise ValueError('Qualification must not mount host folders.')
        volume = worker.docker_json(payload, 'volume', 'inspect', mount['Name'])[0]
        if volume.get('Options') or any(volume.get('Labels', {}).get(key) != value for key, value in owner.items()):
            raise ValueError('The test volume is not private to this qualification.')
    # Docker's legacy Binds field also contains named-volume syntax. The
    # effective Mounts and inspected volume ownership above are authoritative;
    # reject any additional or differently mapped bind declaration.
    binds = settings.get('Binds') or []
    if binds and (len(binds) != 3 or set(binds) != {
            item['Name'] + ':' + item['Destination'] + ':rw' for item in mounts}):
        raise ValueError('Unexpected qualification bind declarations.')
    networks = container.get('NetworkSettings', {}).get('Networks', {})
    if len(networks) != 1:
        raise ValueError('The test service has unexpected network access.')
    network = worker.docker_json(payload, 'network', 'inspect', next(iter(networks)))[0]
    if network.get('Internal') is not True or any(network.get('Labels', {}).get(key) != value for key, value in owner.items()):
        raise ValueError('The test network is not private and offline.')
    tmp = settings.get('Tmpfs', {}).get('/tmp', '')
    if not all(value in tmp.split(',') for value in ('noexec', 'nosuid', 'size=' + str(expected['tmpMiB'] * 1024**2))):
        raise ValueError('The bounded temporary filesystem was not applied.')
    return {'backend': 'docker', 'owner': row['launchOperationId'], 'offline': True,
            'unprivileged': True, 'hostMounts': False, 'limits': expected}
