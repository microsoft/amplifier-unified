"""One global policy over existing qualified storage-retirement owners.

This owner chooses when to ask existing owners to reclaim unused code. It does
not delete files itself, and never treats source/result archives as a cache.
"""
import asyncio
import json
from pathlib import Path
import time
import uuid

from amplifier_operations.coordination import fingerprint

DEFAULTS = {'revision': 0, 'enabled': True, 'keepDays': 7, 'maxUnusedBytes': 10 * 1024**3}
POLICY = 'storage-cleanup-policy'
OBSERVATION = 'storage-cleanup-observation'
LATEST = 'storage-cleanup-latest'


def definitions(schema, string):
    return {
        'storage.cleanup.read': ('Read global automatic cleanup settings and the last receipt without scanning or starting work.', schema({}, [])),
        'storage.cleanup.save': ('User-only: save global unused component/test-runner code retention. Saved test sources, results, chats and working folders are always kept.', schema({
            'expectedRevision': {'type': 'integer', 'minimum': 0}, 'enabled': {'type': 'boolean'},
            'keepDays': {'type': 'integer', 'minimum': 0, 'maximum': 3650},
            'maxUnusedBytes': {'type': 'integer', 'minimum': 0, 'maximum': 2**53-1},
        }, ['expectedRevision', 'enabled', 'keepDays', 'maxUnusedBytes'])),
        'storage.cleanup.preview': ('Measure a bounded set of unused code copies and protected storage. Never starts workers or deletes files. Incomplete measurements are reported.', schema({}, [])),
        'storage.cleanup.run': ('User-only: apply the saved cleanup policy to currently proven unused code. Each owner rechecks references before removal and keeps an outcome receipt.', schema({
            'expectedRevision': {'type': 'integer', 'minimum': 0}}, ['expectedRevision'])),
    }


def record(app, key, default=None):
    row = app.db.execute('SELECT receipt FROM commands WHERE id=?', (key,)).fetchone()
    return json.loads(row[0]).get('result') if row else default


def store(app, key, value):
    app.db.execute('INSERT OR REPLACE INTO commands VALUES(?,?,?)',
        (key, key, json.dumps({'commandAction': key, 'result': value})))
    app.db.commit()


def policy(app):
    return {**DEFAULTS, **(record(app, POLICY, {}) or {})}


def read(app):
    return {'policy': policy(app), 'lastCleanup': record(app, LATEST),
            'scope': 'All workspaces on this Amplifier installation. Only unused component code, test runner code, and qualified reusable test dependencies are eligible.',
            'preserved': 'Chats, working folders, saved test sources and results, other dependency stores, current and rollback versions, and uncertain work are kept.'}


def request_maintenance(app):
    """Wake the single policy owner; a standalone qualifier never removes code."""
    manager = getattr(app, 'update_manager', None)
    if manager is not None:
        manager._storage_policy_due = True
        manager.wakeup.set()


def lock(app):
    if not hasattr(app, '_storage_cleanup_lock'):
        app._storage_cleanup_lock = asyncio.Lock()
    return app._storage_cleanup_lock


def plan(candidates, settings, seen, now):
    rows = [{**item, 'unusedSince': max(seen.get(item['kind'] + ':' + item['id'], now), item.get('lastUsedAt', 0))} for item in candidates]
    total = sum(item['fileBytes'] for item in rows)
    remaining = total
    for row in sorted(rows, key=lambda item: (item['unusedSince'], item['kind'], item['id'])):
        expired = now - row['unusedSince'] >= settings['keepDays'] * 86400
        pressure = bool(settings['maxUnusedBytes'] and remaining > settings['maxUnusedBytes'])
        row['selected'] = expired or pressure
        row['reason'] = ('Retention time reached' if expired else 'Unused copies exceed the saved size target') if row['selected'] else 'Kept by your retention settings'
        if row['selected']:
            remaining -= row['fileBytes']
    return rows


def unresolved(app):
    held = set()
    for value, in app.db.execute("SELECT receipt FROM commands WHERE id LIKE 'storage-cleanup-run:%' AND json_extract(receipt,'$.result.status') IN ('running','interrupted','needs-inspection')"):
        for effect in json.loads(value)["result"].get("effects", []):
            if effect.get("status") == "attempting":
                held.add((effect["kind"], effect["id"]))
    return held


async def preview(app):
    from . import update_retention, managed_runner_updates, test_dependency_layers
    manager = app.update_manager
    settings = policy(app)
    generations = await update_retention.reclaim(manager, inspect_only=True)
    runner_inspection = {'protectedIds': [], 'complete': True}
    try:
        runners = await asyncio.wait_for(managed_runner_updates.retire(app, inspect_only=True, inspection=runner_inspection), 15)
        runner_complete = runner_inspection['complete']
    except (TimeoutError, ValueError, OSError):
        runners, runner_complete = [], False
    try:
        dependencies = await asyncio.to_thread(test_dependency_layers.inspect, app.data_dir)
    except (TimeoutError, ValueError, OSError):
        dependencies = {'candidates': [], 'protectedIds': [], 'complete': False}
    held = unresolved(app)
    candidates = [row for row in generations.get('candidates', []) + (runners or []) + dependencies['candidates'] if (row['kind'], row['id']) not in held]
    seen = record(app, OBSERVATION, {}) or {}
    rows = plan(candidates, settings, seen, time.time())
    protected = []
    eligible = {(row['kind'], row['id']) for row in rows}
    # This is metadata enumeration, not a scan of workspaces or chat history.
    for folder in update_retention._folders(manager):
        if ('ecosystem', folder.name) not in eligible:
            protected.append({'kind': 'ecosystem', 'id': folder.name, 'label': 'Component version kept',
                              'reason': 'Current, rollback, referenced, changed, or not fully verified'})
    for item in app.smart_tools.state.get('installations', []):
        if item.get('runnerCandidate') and ('runner', item['id']) not in eligible:
            protected.append({'kind': 'runner', 'id': item['id'], 'label': 'Test runner version kept',
                              'reason': 'Current, rollback, referenced, changed, or not fully verified'})
    for identity in dependencies['protectedIds']:
        protected.append({'kind': 'test-dependencies', 'id': identity, 'label': 'Reusable test dependencies kept',
                          'reason': 'Referenced, changed, or not fully verified'})
    # Preserve all test artifacts and engine roots. A terminal chat is not
    # proof that a native launch cannot still finish, nor an archive grant.
    for row in protected:
        if (row['kind'], row.get('id')) in held:
            row['reason'] = 'An earlier cleanup has an unconfirmed outcome; its files are kept for inspection'
    protected.append({'kind': 'evidence', 'label': 'Saved work and test evidence',
                      'reason': 'Source/result archives, working folders and histories are never cleanup targets'})
    protected_measured = 0
    protected_complete = True
    measured_paths = {path for row in candidates for path in row.get('paths', [])}
    from .runtime_environment import project_path
    for index, row in enumerate(protected):
        if not row.get('id') or index >= 4 or row['kind'] == 'test-dependencies':
            row['fileBytes'] = None
            protected_complete = False
            continue
        paths = ([str(manager.directory / 'releases' / row['id']), str(project_path(manager.home, row['id']))]
                 if row['kind'] == 'ecosystem' else [str(Path(app.data_dir) / 'smart-tools/installs' / row['id'])])
        paths = [path for path in paths if path not in measured_paths and Path(path).exists()]
        try:
            measured = await update_retention._probe('measure', {'paths': paths}, timeout=5)
            row['fileBytes'] = measured['fileBytes']
            protected_measured += row['fileBytes']
            measured_paths.update(paths)
        except (ValueError, OSError, TimeoutError):
            row['fileBytes'] = None
            protected_complete = False
    complete = generations.get('complete', False) and runner_complete and dependencies['complete']
    return {'policy': settings, 'rows': [{k: v for k, v in row.items() if k not in {'paths', 'stamp'}} for row in rows],
            'protected': protected[:128], 'protectedCount': len(protected),
            'observedProtected': ['ecosystem:' + key for key in generations.get('protectedIds', [])]
                + ['runner:' + key for key in runner_inspection['protectedIds']]
                + ['test-dependencies:' + key for key in dependencies['protectedIds']],
            'protectedFileBytes': protected_measured, 'protectedMeasurementComplete': protected_complete,
            'unusedFileBytes': sum(row['fileBytes'] for row in rows),
            'selectedFileBytes': sum(row['fileBytes'] for row in rows if row['selected']),
            'complete': complete, 'measuredAt': time.time(),
            'measurementNote': 'File content, not guaranteed disk space freed. Shared runtime folders are counted once; hard-linked files are counted once per copy, and shared or copy-on-write blocks can remain. Some protected storage is deliberately left unmeasured.',
            'detail': generations.get('reason') or (None if runner_complete else 'Runner inspection reached its time limit; those copies were kept.')}


async def run(app, identity, *, expected_revision=None, automatic=False):
    from . import update_retention, managed_runner_updates, test_dependency_layers
    async with lock(app):
        key = 'storage-cleanup-run:' + identity
        previous = record(app, key)
        if previous is not None:
            if previous.get('expectedRevision') != expected_revision:
                raise ValueError('This cleanup request ID was used with different contents.')
            return previous  # Re-observe the same attempt; never submit it anew.
        settings = policy(app)
        if expected_revision is not None and settings['revision'] != expected_revision:
            raise ValueError('Cleanup settings changed. Reload them before cleaning up.')
        if automatic and not settings['enabled']:
            return {'status': 'disabled', 'removedFileBytes': 0}
        report = await preview(app)
        if policy(app) != settings or update_retention.maintenance_blocked(app.update_manager):
            return {'status': 'deferred', 'removedFileBytes': 0, 'detail': 'Settings or update activity changed. Existing files were kept.'}
        seen = record(app, OBSERVATION, {}) or {}
        now = time.time()
        observed = {row['kind'] + ':' + row['id'] for row in report['rows']}
        protected = set(report.get('observedProtected', []))
        # Do not reset unseen rows just because the bounded inspection yielded.
        seen = {key: value for key, value in seen.items() if key not in protected}
        for name in observed:
            seen.setdefault(name, now)
        store(app, OBSERVATION, seen)
        result = {'id': identity, 'expectedRevision': expected_revision, 'status': 'running', 'startedAt': now, 'policy': settings,
                  'selectedFileBytes': report['selectedFileBytes'], 'removedFileBytes': 0,
                  'effects': [], 'completeInspection': report['complete']}
        store(app, key, result)
        store(app, LATEST, result)
        chosen = {(row['kind'], row['id']): row for row in report['rows'] if row['selected']}
        def evidence(status, item):
            if status == 'attempting' and policy(app) != settings:
                raise ValueError('Cleanup settings changed before removal; files were kept.')
            effect = next((row for row in result['effects'] if (row['kind'], row['id']) == (item['kind'], item['id'])), None)
            if effect is None:
                effect = {**item, 'fileBytes': item.get('fileBytes', chosen[(item['kind'], item['id'])]['fileBytes'])}
                result['effects'].append(effect)
            effect['status'] = status
            result['removedFileBytes'] = sum(row['fileBytes'] for row in result['effects'] if row['status'] == 'completed')
            store(app, key, result)
            store(app, LATEST, result)
        try:
            await update_retention.reclaim(app.update_manager,
                selected={identity for kind, identity in chosen if kind == 'ecosystem'}, evidence=evidence)
            if policy(app) == settings:
                await managed_runner_updates.retire(app,
                    selected={identity for kind, identity in chosen if kind == 'runner'}, evidence=evidence)
            if policy(app) == settings:
                loop = asyncio.get_running_loop()
                async def dependency_evidence(status, item):
                    evidence(status, item)
                def record_dependency(status, item):
                    # SQLite/policy stay on their owning app loop. The native
                    # remover calls this again at its final deletion boundary.
                    asyncio.run_coroutine_threadsafe(dependency_evidence(status, item), loop).result(timeout=10)
                await asyncio.to_thread(test_dependency_layers.retire, app.data_dir,
                    {identity: row for (kind, identity), row in chosen.items() if kind == 'test-dependencies'}, record_dependency)
            result['status'] = 'needs-inspection' if any(row['status'] == 'attempting' for row in result['effects']) else 'completed'
        finally:
            if result['status'] == 'running':
                result['status'] = 'interrupted'
            result['finishedAt'] = time.time()
            store(app, key, result)
            store(app, LATEST, result)
        return result


async def dispatch(app, action, args, origin, command_id):
    from .service import AppError
    if action == 'storage.cleanup.read':
        return {'accepted': True, 'result': read(app)}
    if action == 'storage.cleanup.preview':
        async with lock(app):
            return {'accepted': True, 'result': await preview(app)}
    if origin not in {'ui', 'user'}:
        raise AppError('Only the user can change global cleanup settings or request cleanup.', 403)
    identity = command_id or str(uuid.uuid4())
    if action == 'storage.cleanup.run':
        try:
            return {'accepted': True, 'result': await run(app, identity, expected_revision=args['expectedRevision'])}
        except ValueError as exc:
            raise AppError(str(exc), 409) from None
    digest = fingerprint([action, args])
    async with app.lock:
        saved = app.db.execute('SELECT fingerprint,receipt FROM commands WHERE id=?', (identity,)).fetchone()
        if saved:
            if saved[0] != digest:
                raise AppError('This command ID was used with different contents.', 409)
            return {**json.loads(saved[1]), 'duplicate': True}
        current = policy(app)
        if args['expectedRevision'] != current['revision']:
            raise AppError('Cleanup settings changed. Reload them before saving.', 409)
        value = {name: args[name] for name in ('enabled', 'keepDays', 'maxUnusedBytes')}
        if (type(value['enabled']) is not bool or type(value['keepDays']) is not int or not 0 <= value['keepDays'] <= 3650
                or type(value['maxUnusedBytes']) is not int or not 0 <= value['maxUnusedBytes'] <= 2**53-1):
            raise AppError('Choose valid cleanup settings.', 400)
        value['revision'] = current['revision'] + 1
        receipt = {'accepted': True, 'commandAction': action, 'result': {**read(app), 'policy': value}}
        try:
            app.db.execute('INSERT OR REPLACE INTO commands VALUES(?,?,?)', (POLICY, POLICY, json.dumps({'result': value})))
            app.db.execute('INSERT INTO commands VALUES(?,?,?)', (identity, digest, json.dumps(receipt)))
            app.db.commit()
        except BaseException:
            app.db.rollback()
            raise
        request_maintenance(app)
        return receipt
