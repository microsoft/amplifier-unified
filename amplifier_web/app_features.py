"""Explicit additive app features, qualified against the unchanged serving app.

This is updater intent and receipt validation, not a second installer. All
installation, busy admission, replacement and restart remain in app_updates.
"""
import copy
import json
from importlib import metadata
from pathlib import Path
import re
import time


def running_application(manager):
    from . import app_updates
    distribution = metadata.distribution('amplifier-unified')
    if Path(distribution.locate_file('amplifier_web')).resolve() != Path(__file__).parent.resolve():
        raise ValueError('Feature installation requires the serving packaged app, not a development checkout.')
    direct = json.loads(distribution.read_text('direct_url.json') or 'null')
    row = app_updates.components.normalized([{'name': 'amplifier-unified', 'version': distribution.version, 'direct': direct}])[0]
    if (row.get('url', '').removesuffix('.git') != app_updates.SOURCE
            or row.get('subdirectory')
            or row.get('revision') != manager.running_identity.get('revision')
            or row['version'] != app_updates.__version__):
        raise ValueError('The serving app must have verified Microsoft source provenance before adding a feature.')
    return {**row, 'url': app_updates.SOURCE}


def status(manager):
    if manager is None:
        return {'supported': False, 'reason': 'Feature installation requires the managed Unified app updater.'}
    from .app_updates import installed_extras
    try:
        app = running_application(manager)
    except (ValueError, OSError, metadata.PackageNotFoundError):
        return {'supported': False, 'reason': 'This host cannot verify its packaged Microsoft app source. Use its deployment owner to install a qualified app before adding native observation.'}
    state = manager.service.state['updates']
    return {'supported': True, 'appRevision': app['revision'], 'appVersion': app['version'],
        'installedExtras': installed_extras(),
        'pending': bool(manager.lock.locked() or manager.awaiting_restart() or state.get('pendingApp') or state.get('pendingRelease')),
        'action': 'updates.featureInstall', 'feature': 'native-desktop',
        'detail': 'Add native observation to this same app revision, preserving installed components and optional features. The app restarts when all work is idle. This installs no desktop-control grant and requests no OS permission.'}


def selection(manager, feature, request_id):
    from .app_updates import installed_extras, validated_extras
    if feature != 'native-desktop':
        raise ValueError('Only the native-desktop addition is supported by this action.')
    baseline = validated_extras(installed_extras())
    return {'kind': 'add-feature', 'feature': feature, 'requestId': request_id,
        'hostApp': running_application(manager), 'baselineExtras': baseline,
        'extras': validated_extras(sorted(set(baseline) | {feature}))}


def validate_selection(manager, selected, extras, *, installing=True):
    from .app_updates import installed_extras, validated_extras
    if (not isinstance(selected, dict) or selected.get('kind') != 'add-feature'
            or selected.get('feature') != 'native-desktop'
            or not isinstance(selected.get('requestId'), str) or not selected['requestId']):
        raise ValueError('Invalid optional-feature request receipt.')
    baseline = validated_extras(selected.get('baselineExtras'))
    desired = validated_extras(selected.get('extras'))
    if desired != sorted(set(baseline) | {'native-desktop'}) or desired != extras:
        raise ValueError('The qualified feature selection changed.')
    if (selected.get('hostApp') != running_application(manager)
            or installing and baseline != validated_extras(installed_extras())):
        raise ValueError('The serving app or its optional features changed after qualification.')
    return desired


def require_preserved_components(candidate, baseline):
    by_name = {row['name']: row for row in candidate}
    if any(by_name.get(row['name']) != row for row in baseline):
        raise ValueError('Adding a feature must preserve every installed component; resolve the dependency conflict before retrying.')


def valid_restart_selection(target):
    """Reject malformed optional restart intent before startup reconciliation."""
    from .app_updates import SOURCE, components, validated_extras
    selected = target.get('featureSelection')
    if selected is None and 'featureSelection' not in target:
        return 'dependencyDigest' not in target
    try:
        if (not isinstance(selected, dict) or selected.get('kind') != 'add-feature'
                or selected.get('feature') != 'native-desktop'
                or not isinstance(selected.get('requestId'), str) or not selected['requestId']):
            return False
        baseline = validated_extras(selected.get('baselineExtras'))
        if validated_extras(selected.get('extras')) != sorted(set(baseline) | {'native-desktop'}):
            return False
        app = components.validate_graph([selected.get('hostApp')])[0]
        return (app['name'] == 'amplifier-unified' and app.get('url') == SOURCE and not app.get('subdirectory')
            and app.get('revision') == target.get('revision') and app['version'] == target.get('version')
            and isinstance(target.get('dependencyDigest'), str)
            and re.fullmatch('[a-f0-9]{64}', target['dependencyDigest']) is not None)
    except (ValueError, TypeError):
        return False


async def record(manager, request_id, phase, **details):
    async with manager.service.lock:
        rows = manager.service.state['updates'].setdefault('featureResults', {})
        rows[request_id] = {**rows.get(request_id, {}), 'requestId': request_id,
            'feature': 'native-desktop', 'phase': phase, 'updatedAt': time.time(), **copy.deepcopy(details)}
        while len(rows) > 20:
            rows.pop(next(iter(rows)))
        manager.service._publish()


def reconcile_requests(state):
    """An interrupted qualification is not replayed or marked installed."""
    pending = state.get('pendingRestart') or state.get('pendingApp') or {}
    selected = pending.get('featureSelection') if isinstance(pending, dict) else None
    request_id = selected.get('requestId') if isinstance(selected, dict) else None
    rows = state.get('featureResults', {})
    if not isinstance(rows, dict):
        state['featureResults'] = {}
        return
    for identity, row in rows.items():
        if not isinstance(row, dict):
            row = rows[identity] = {'requestId':identity, 'feature':'native-desktop', 'phase':'queued'}
        if row.get('phase') in {'queued', 'staging', 'qualified', 'activating', 'restart_pending'}:
            if identity != request_id:
                row.update(phase='interrupted', updatedAt=time.time(),
                    detail='The feature request was interrupted. No installation or restart was replayed; inspect its diagnostic receipt before retrying.')
