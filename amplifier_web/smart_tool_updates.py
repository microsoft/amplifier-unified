"""Updates for app-owned packages; external MCP services remain owner-managed."""
import asyncio
import copy
import os
from pathlib import Path
import time


class ToolQualificationError(ValueError):
    def __init__(self, reason='smart-tool-startup-failed'):
        super().__init__('The updated tool could not complete its connection check. Its existing connection is unchanged.')
        self.diagnostic_facts = {'reason': reason}


def retarget(row, previous, target):
    if row.get('transport', 'stdio') != 'stdio':
        raise ValueError('Remote MCP services are updated by their owner.')
    old_bin, new_bin = Path(previous['binDir']), Path(target['binDir'])
    command = Path(row['command'])
    if not command.is_absolute() or command.parent != old_bin:
        raise ValueError('The connection uses a custom executable. Update its command manually.')
    candidate = new_bin / command.name
    if not candidate.is_file():
        raise ValueError('The updated tool no longer provides the configured executable.')
    result = copy.deepcopy(row)
    result.update(command=str(candidate), installationId=target['id'], tools=[], loadedSchemas={}, catalogState='stale')
    old_source, new_source = previous.get('sourceDir'), target.get('sourceDir')
    def path(value):
        if old_source and new_source and isinstance(value, str):
            if value == old_source or value.startswith(old_source + os.sep):
                return new_source + value[len(old_source):]
        return value
    result['args'] = [path(value) for value in row.get('args', [])]
    result['cwd'] = path(row.get('cwd'))
    return result


class ManagedUpdates:
    async def discard_update(self, previous_id, revision):
        """Remove only unused candidates belonging to this skipped revision.

        Installation records are durable before qualification. Recover from
        those records even if qualification failed before the batch got an ID.
        Uninstall rechecks stage identity under its lock and keeps all existing
        path, connection, and test-environment ownership guards.
        """
        if not revision:
            return
        candidates = [row['id'] for row in self.state['installations']
                      if row.get('stagedFrom') == previous_id and row.get('commit') == revision]
        for target_id in candidates:
            await self.uninstall(target_id, staged_from=previous_id, revision=revision)

    def update_sources(self):
        from .updates import pinned, safe_label
        rows = []
        for item in self.state['installations']:
            if item.get('supersededBy') or item.get('stagedFrom') or item.get('runnerCandidate'): continue
            repository, ref = item.get('repository'), item.get('ref') or 'HEAD'
            if not repository or not item.get('commit'): continue
            servers = [row for row in self.state['servers'] if row.get('installationId') == item['id']]
            managed = all(row.get('transport', 'stdio') == 'stdio' and
                          Path(row.get('command', '')).parent == Path(item['binDir']) for row in servers)
            rows.append({'id': 'smart-tool:' + item['id'], 'installationId': item['id'],
                         'kind': 'smart tool', 'label': item.get('name') or safe_label(repository),
                         'url': repository, 'ref': ref, 'current': item['commit'], 'updateTier': 'other',
                         'eligible': managed and not pinned(ref),
                         'status': 'local' if not managed else 'pinned' if pinned(ref) else 'not_checked',
                         'usage': 'configured' if servers else 'installed',
                         'usageEvidence': ['App-managed Smart Tool'],
                         'detail': 'Custom connection commands are kept as configured.' if not managed else 'Includes package and configured MCP connections.'})
        for row in self.state['servers']:
            if not row.get('installationId'):
                rows.append({'id': 'external-tool:' + row['id'], 'kind': 'smart tool',
                             'label': row['name'], 'status': 'local', 'eligible': False,
                             'updateTier': 'other', 'usage': 'configured', 'usageEvidence': ['External MCP connection'],
                             'detail': 'Updated by its service owner or external package manager.'})
        from .managed_runner_updates import update_sources
        runner_rows = update_sources(self.service)
        selected = {row['installationId'] for row in runner_rows if row.get('installationId')}
        # The runner has a separate source policy. Never mutate a user's MCP
        # connection or infer its pin from this runner's installed commit.
        rows = [row for row in rows if row.get('installationId') not in selected
                or any(server.get('installationId') == row.get('installationId') for server in self.state['servers'])]
        return [*rows, *runner_rows]

    async def stage_update(self, source):
        if source.get('runnerKind'):
            from .managed_runner_backend import backend
            return await backend(source['runnerKind']).stage(self.service, source)
        previous = next(row for row in self.state['installations'] if row['id'] == source['installationId'])
        target = await self.install({'repository': previous['repository'], 'ref': source['latest'],
                                     'path': previous.get('path'), 'extras': previous.get('extras', []), '_update_from': previous['id']})
        if target['commit'] != source['latest']: raise ValueError('The tool revision changed during installation.')
        # Installing by immutable revision must not change the user's update policy.
        await self._change(lambda state: next(row for row in state['installations'] if row['id'] == target['id']).update(
            ref=previous.get('ref') or 'HEAD', stagedFrom=previous['id']))
        await self.validate_update(previous['id'], target['id'])
        return target

    async def _prepare_update_connection(self, row):
        from .mcp_connection import Connection
        secrets = {}
        for name, variable in row.get('env', {}).items():
            if not os.environ.get(variable):
                raise ToolQualificationError('missing-configured-environment')
            secrets[name] = os.environ[variable]
        connection = Connection(row, secrets)
        try:
            info = await asyncio.wait_for(asyncio.shield(connection.ready), 30)
            definitions, values = await self._discover_catalog(connection)
            row.update(values)
            row.update(self._redact(info), status='connected', connectionState='ready', error=None, updatedAt=time.time())
            return connection, definitions
        except BaseException as error:
            await connection.close()
            if connection.ready.done() and not connection.ready.cancelled():
                connection.ready.exception()
            if not isinstance(error, Exception):
                raise
            raise ToolQualificationError() from error

    async def validate_update(self, previous_id, target_id):
        """Check exact configured MCP commands before declaring a stage ready.

        Initialization and catalog discovery only; never invoke a tool, replace
        a registration, or change a saved server’s connection state.
        Activation repeats qualification because configuration may change.
        """
        previous = next(row for row in self.state['installations'] if row['id'] == previous_id)
        target = next(row for row in self.state['installations'] if row['id'] == target_id)
        for original in self.state['servers']:
            if original.get('installationId') != previous_id:
                continue
            candidate = retarget(original, previous, target)
            connection, _ = await self._prepare_update_connection(candidate)
            await connection.close()

    async def activate_update(self, previous_id, target_id):
        """Caller holds the app's idle/admission barrier. Never run a tool call."""
        from .smart_tools import configuration_key
        previous = next(row for row in self.state['installations'] if row['id'] == previous_id)
        target = next(row for row in self.state['installations'] if row['id'] == target_id)
        originals = {row['id']: copy.deepcopy(row) for row in self.state['servers'] if row.get('installationId') == previous_id}
        candidates = {key: retarget(row, previous, target) for key, row in originals.items()}
        old_connections = {key: self.connections.get(key) for key in originals}
        active = {key for key, connection in old_connections.items() if connection and not connection.task.done() and not connection.failure}
        old_schemas = {key: self.schemas.get(key) for key in originals}
        connections, catalogs, committed = {}, {}, False
        installed_before = copy.deepcopy(self.state['installations'])
        replaced = False
        try:
            # Handshake and discovery validate the exact command/arguments and
            # referenced environment. No inference, sampling or tool call occurs.
            for key, row in candidates.items():
                connection, definitions = await self._prepare_update_connection(row)
                connections[key] = connection
                catalogs[key] = definitions
            async with self.install_lock:
                if any(configuration_key(self._server(key)) != configuration_key(row) or self.connections.get(key) is not old_connections[key] for key, row in originals.items()):
                    raise ValueError('A connection changed while the update was prepared. Check again.')
                def replace(state):
                    nonlocal replaced
                    # One durable snapshot binds new commands, schemas and the
                    # rollback pointer only after every connection is qualified.
                    if any(candidates[key]['catalogEpoch'] != connection.catalog_epoch or connection.task.done()
                           for key, connection in connections.items()):
                        raise ValueError('A tool changed or disconnected before activation. Check again.')
                    replaced = True
                    for key, connection in connections.items():
                        if key in active:
                            self.connections[key] = connection
                            self.schemas[key] = catalogs[key]
                        else:
                            self.connections.pop(key, None)
                            self.schemas.pop(key, None)
                            candidates[key].update(status='disconnected', connectionState='disconnected', catalogState='stale', loadedSchemas={})
                    state['servers'] = [candidates.get(row['id'], row) for row in state['servers']]
                    previous['supersededBy'] = target_id
                    target.pop('supersededBy', None)
                    target.pop('stagedFrom', None)
                    target['previousInstallationId'] = previous_id
                await self._change(replace)
                committed = True
                for key in active: connections[key].changed = self._connection_changed
            await asyncio.gather(*(connection.close() for connection in [*old_connections.values(), *(connection for key, connection in connections.items() if key not in active)] if connection), return_exceptions=True)
            return {'installationId': target_id, 'previousInstallationId': previous_id}
        except BaseException:
            if committed:
                raise  # Retiring old transports must never undo the committed switch.
            if replaced:
                for key, connection in old_connections.items():
                    if connection: self.connections[key] = connection
                    else: self.connections.pop(key, None)
                    if old_schemas[key] is not None: self.schemas[key] = old_schemas[key]
                    else: self.schemas.pop(key, None)
                await self._change(lambda state: state.update(
                    servers=[originals.get(row['id'], row) for row in state['servers']], installations=installed_before))
            await asyncio.gather(*(connection.close() for connection in connections.values()), return_exceptions=True)
            for connection in connections.values():
                if connection.ready.done() and not connection.ready.cancelled(): connection.ready.exception()
            raise
