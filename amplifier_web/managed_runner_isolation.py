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
    raise ValueError('Docker uses the installed Smart Tool, not target-local certification.')
