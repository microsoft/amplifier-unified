"""Confirm a replacement host through its listener, never a package probe.

Identity is captured before this process can replace its installation. Restart
receipts belong to one attempt; a healthy newer host does not retroactively
turn an old failed systemctl command into a successful command.
"""
from __future__ import annotations

import asyncio
import json
from importlib import metadata
from pathlib import Path
import re
import time
import uuid

import aiohttp

from . import __version__

LEGACY_RESTART_ERROR = 'The app installed, but the managed service could not restart. Run amplifier-unified service restart.'


def running_identity():
    revision = None
    try:
        distribution = metadata.distribution('amplifier-unified')
        # Do not attest a different installed distribution when running a checkout.
        if Path(distribution.locate_file('amplifier_web')).resolve() == Path(__file__).parent.resolve():
            source = json.loads(distribution.read_text('direct_url.json') or '{}')
            value = source.get('vcs_info', {}).get('commit_id')
            if isinstance(value, str) and re.fullmatch('[a-f0-9]{40}', value):
                revision = value
    except (metadata.PackageNotFoundError, OSError, ValueError, AttributeError):
        pass
    return {'version': __version__, 'revision': revision, 'instanceId': uuid.uuid4().hex}


def valid_target(value):
    return (isinstance(value, dict)
            and isinstance(value.get('version'), str) and re.fullmatch(r'\d+\.\d+\.\d+', value['version'])
            and isinstance(value.get('revision'), str) and re.fullmatch('[a-f0-9]{40}', value['revision'])
            and isinstance(value.get('attemptId'), str) and re.fullmatch('[a-f0-9]{32}', value['attemptId']))


def recovery_candidate(manager):
    state = manager.service.state['updates']
    pending = state.get('pendingRestart')
    if pending:
        return dict(pending) if valid_target(pending) else None
    # Older updaters erase the marker after systemd terminates their child.
    # Recover only that precise failure, corroborated by its validation receipt
    # and successful replacement probe. Never infer readiness from version alone.
    if state.get('phase') != 'error' or state.get('error') != LEGACY_RESTART_ERROR:
        return None
    diagnostics = state.get('diagnostics', {})
    failure = diagnostics.get('lastFailure') or {}
    if (failure.get('kind') != 'application' or failure.get('phase') != 'service-restart'
            or failure.get('status') not in {'failed', 'interrupted'}
            or failure.get('attemptId') != diagnostics.get('attemptId')
            or failure.get('revision') != diagnostics.get('revision')):
        return None
    revision = failure.get('revision', '')
    if not isinstance(revision, str) or not re.fullmatch('[a-f0-9]{40}', revision):
        return None
    directory = manager.directory / 'applications' / revision
    paths = [directory / 'validated.json']
    # New update attempts keep separate immutable receipts. Match the precise
    # failed attempt, never a newer candidate for the same app version/revision.
    try:
        paths.extend(child / 'validated.json' for child in directory.iterdir()
                     if not child.is_symlink() and child.is_dir()
                     and re.fullmatch('[a-f0-9]{32}', child.name))
    except OSError:
        pass
    matches = []
    for path in paths:
        try:
            candidate = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if (not valid_target(candidate) or candidate['attemptId'] != failure.get('attemptId')
                or candidate['revision'] != revision):
            continue
        if path.parent != directory and candidate.get('generation') != path.parent.name:
            continue
        matches.append(candidate)
    if len(matches) != 1:
        return None
    validated = matches[0]
    if not any(event.get('attemptId') == validated['attemptId'] and event.get('revision') == revision
               and event.get('kind') == 'application' and event.get('phase') == 'replacement-probe'
               and event.get('status') == 'succeeded' and event.get('probe', {}).get('ok') is True
               and event.get('probe', {}).get('version') == validated['version']
               for event in diagnostics.get('events', [])):
        return None
    return {**validated, 'legacy': True}


def same_target(first, second):
    return bool(first and second and all(first.get(key) == second.get(key)
                for key in ('attemptId', 'version', 'revision', 'sourceInstanceId', 'legacy')))


async def confirm_readiness(manager, health, expected=None, command_id=None):
    """Consume an authenticated HTTP probe of this exact running host."""
    from .auth import data_identity
    async with manager.lock:
        target = recovery_candidate(manager)
        identity = manager.running_identity
        if (not target or (expected is not None and not same_target(target, expected))
                or not isinstance(health, dict) or health.get('ok') is not True
                or health.get('app') != 'amplifier-unified'
                or health.get('dataIdentity') != data_identity(manager.home)
                or any(health.get(key) != identity[key] for key in ('version', 'revision', 'instanceId'))
                or any(identity[key] != target[key] for key in ('version', 'revision'))
                or target.get('sourceInstanceId') == identity['instanceId']):
            return False
        async with manager.service.lock:
            state = manager.service.state['updates']
            manager.diagnostics.begin('application', target['revision'], target['attemptId'])
            if target.get('legacy'):
                # Keep the historical failure in the receipt; this is current
                # health reconciliation, not a fabricated historical restart-ack.
                state['reconciliation'] = {key: target[key] for key in ('attemptId', 'version', 'revision')}
                state['reconciliation']['verifiedAt'] = time.time()
                detail = 'The current installation is healthy. The previous restart command issue is retained in the update history; no additional restart is needed.'
                phase = 'restart-reconcile'
            else:
                manager.diagnostics.clear_failure()
                state.pop('reconciliation', None)
                detail = 'Application update installed and the restarted server is healthy.'
                phase = 'restart-ack'
            application = state.get('application', {})
            application.pop('componentUpdates', None)
            application.update(status='current', current=identity['version'])
            state['items'] = [application if row.get('id') == 'application' else row for row in state.get('items', [])]
            state['available'] = sum(row.get('status') == 'update' for row in state['items'])
            state.update(phase='installed', pendingRestart=None, pendingApp=None, appAvailable=False,
                         installedAt=time.time(), error=None, detail=detail)
            if command_id:
                manager.diagnostics.record('restart-readiness', 'succeeded', commandId=command_id,
                                           observedVersion=identity['version'], observedRevision=identity['revision'])
            manager.diagnostics.record(phase, 'succeeded', observedVersion=identity['version'],
                                       observedRevision=identity['revision'])
            manager.service._publish()
        return True


def probe_targets(manager):
    """Connect only to configured local bind addresses, with exact TLS pinning.

    Pin the configured leaf certificate so custom CA/DNS deployments work
    without routing a control token through a proxy or disabling TLS checks.
    """
    from .deployment import resolve_path
    config = manager.service.server_config
    secure = config['tls']['method'] != 'none'
    pin = None
    if secure:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes
        certificate = x509.load_pem_x509_certificate(resolve_path(manager.home, config['tls']['cert']).read_bytes())
        pin = aiohttp.Fingerprint(certificate.fingerprint(hashes.SHA256()))
    urls = []
    for host in config['bind']:
        host = {'0.0.0.0': '127.0.0.1', '::': '::1'}.get(host, host)
        host = '[' + host + ']' if ':' in host else host
        urls.append(f"{'https' if secure else 'http'}://{host}:{manager.service.port}/api/health")
    return list(dict.fromkeys(urls)), pin


async def wait_for_readiness(manager, token, *, timeout=60, interval=.25):
    target = recovery_candidate(manager)
    if not target:
        return
    # This task starts during aiohttp startup, but acknowledges only after an
    # actual request succeeds. on_startup itself precedes listener readiness.
    diagnostics = manager.diagnostics
    diagnostics.begin('application', target['revision'], target['attemptId'])
    historical_failure = diagnostics.state.get('lastFailure') if target.get('legacy') else None
    command_id = uuid.uuid4().hex
    diagnostics.record('restart-readiness', 'started', commandId=command_id,
                       expectedVersion=target['version'], expectedRevision=target['revision'])
    issue = None
    try:
        urls, pin = probe_targets(manager)
        deadline = asyncio.get_running_loop().time() + timeout
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=2), trust_env=False) as client:
            while not manager.closed and asyncio.get_running_loop().time() < deadline:
                for url in urls:
                    try:
                        async with client.get(url, ssl=pin, allow_redirects=False, headers={
                                'Authorization': 'Bearer ' + token, 'Host': f'localhost:{manager.service.port}'}) as response:
                            if response.status == 200 and await confirm_readiness(manager, await response.json(), expected=target, command_id=command_id):
                                return
                    except (aiohttp.ClientError, OSError, TimeoutError, ValueError):
                        pass
                await asyncio.sleep(interval)
        issue = ('TimeoutError', 'failed')
    except asyncio.CancelledError:
        issue = ('CancelledError', 'interrupted')
        raise
    except (OSError, ValueError):
        issue = ('ValueError', 'failed')
    finally:
        if issue:
            async with manager.lock:
                async with manager.service.lock:
                    if same_target(recovery_candidate(manager), target):
                        error_type, status = issue
                        diagnostics.record('restart-readiness', status, commandId=command_id, errorType=error_type,
                                           expectedVersion=target['version'], expectedRevision=target['revision'],
                                           observedVersion=manager.running_identity['version'],
                                           observedRevision=manager.running_identity['revision'])
                        if historical_failure:
                            diagnostics.state['lastFailure'] = historical_failure
                        else:
                            manager.service.state['updates'].update(phase='activating', error='The installed release has not been confirmed by the running server. Check the service status and update receipt before restarting.')
                        manager.service._publish()
