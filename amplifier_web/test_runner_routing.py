"""Advisory first-fit accounting for one bounded queue pass, not a reservation."""
from __future__ import annotations

from copy import deepcopy

from . import test_environment_profiles as profiles


class RoutingPass:
    def __init__(self, preparing=None, unadmitted=None):
        self.preparing = preparing or {}
        self.unadmitted = self.preparing if unadmitted is None else unadmitted
        self.backends = {}
        self.machines = {}
        self.backend_observations = {}
        self.machine_observations = {}

    @staticmethod
    def request(job):
        # Older saved checks predate frozen resource requests; their execution
        # path already uses the original profile defaults, not today's settings.
        value = job.get('resourceRequest')
        return value if value is not None else profiles.freeze(job.get('profileId', 'linux-python'))

    @staticmethod
    def backend_key(key, config, observation):
        from .registered_test_runners import digest
        runner = config.get('remoteRunner') or {}
        if runner.get('machineId') and runner.get('accountId') and runner.get('backendIdentity'):
            return ('remote', runner['machineId'], runner['accountId'], digest(runner['backendIdentity']))
        scope = observation.get('scope')
        if not runner and isinstance(scope, dict) and scope.get('daemon'):
            return ('local', digest(scope))
        return ('configuration', key)

    @staticmethod
    def machine_key(key, config, observation):
        machine = observation.get('machineResources') or {}
        if not machine.get('supported'):
            return None
        runner = config.get('remoteRunner') or {}
        if runner.get('machineId') and runner.get('accountId'):
            # Use the target account authority, not cloned OS machine IDs.
            return ('remote', runner['machineId'], runner['accountId'])
        if not runner and machine.get('machineId'):
            return ('local', machine['machineId'])
        return ('configuration', key)

    def observe(self, key, config, observation):
        backend = self.backend_key(key, config, observation)
        self.backend_observations.setdefault(backend, deepcopy(observation))
        machine = self.machine_key(key, config, observation)
        if machine is not None:
            self.machine_observations.setdefault(machine, deepcopy(observation['machineResources']))
        return backend, machine

    def room(self, key, config, observation, request):
        from .registered_test_runners import has_room
        if not observation.get('available'):
            return False
        backend, machine_key = self.observe(key, config, observation)
        planned = self.backends.get(backend, 0)
        pending = self.preparing.get(key, 0)
        initial = self.backend_observations[backend]
        limits, counts = initial.get('limits', {}), initial.get('counts', {})
        # Preserve the bounded pass on uncapped backends. Neither this hint nor
        # a positive physical observation replaces final atomic admission.
        maximum = limits.get('max_running', 1)
        if (maximum and counts.get('active', 0) + pending + planned >= maximum) or planned >= 64:
            return False
        retained = limits.get('max_instances', 0)
        total = counts.get('total', counts.get('active', 0) + counts.get('stopped_retained', 0))
        # Starting spans preparation and an already admitted native launch.
        # Only the former still needs a retained slot beyond the backend total.
        if retained and total + self.unadmitted.get(key, 0) + planned >= retained:
            return False
        # A later alias/project observation may already include our first
        # dispatch. Keep one baseline per authority so planned demand is not
        # counted twice. Each real dispatch still rechecks current policy.
        machine = self.machine_observations.get(machine_key, {})
        resources = self.machines.get(machine_key, {})
        return has_room(machine, request, resources)

    def scheduled(self, key, config, observation, request):
        backend, machine = self.observe(key, config, observation)
        self.backends[backend] = self.backends.get(backend, 0) + 1
        if machine is None:
            return
        resources = self.machines.setdefault(machine, {'cpus': 0, 'memoryMiB': 0, 'preparing': 0})
        resources['cpus'] += request['cpus']
        resources['memoryMiB'] += request['memoryMiB']
        resources['preparing'] += int(profiles.get(request['profileId'])['preparationRequired'])
