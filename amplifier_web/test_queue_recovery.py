"""Additive inspection of exact later cleanup; original queue receipts stay intact."""
import sqlite3


def outcome(app, receipt):
    from . import task_environments as environments

    job = receipt.get('job', {})
    if job.get('status') not in {'unknown', 'failed'} or not job.get('resourceId'):
        return None
    sid, rid = receipt['senderSessionId'], job['resourceId']
    try:
        row = environments.get(app.data_dir, sid, rid)
        if (row.get('sessionId') != sid or row.get('id') != rid
                or row.get('kind') != environments.KIND
                or row.get('commandId') != receipt['requestId'] + ':start:' + str(job.get('attempt', 0))
                or row.get('owner') != job.get('actor')
                or row.get('backendKey') != job.get('backendKey')
                or row.get('source') != job.get('source')
                or row.get('profileId', environments.PROFILE) != job.get('profileId')
                or not isinstance(row.get('universeId'), str) or not row['universeId']
                or row.get('status') != 'retired' or row.get('environmentState') != 'removed'
                or any(row.get(key) for key in ('pendingRequest', 'pendingRecovery', 'preparationPending'))):
            return None
        saved = row.get('lastOperation') or {}
        cleanup = app.operations.requests.read(sid, saved['requestId'])
        if (cleanup != saved or not environments.confirmed_result(row, cleanup)
                or cleanup.get('action') not in {'workspace.environments.remove', 'workspace.environments.finishCleanup'}
                or cleanup.get('state') != 'completed' or cleanup['result'].get('ok') is not True
                or cleanup['result'].get('status') != 'removed'
                or cleanup['result'].get('universeId') != row.get('universeId')):
            return None
        check_id = receipt['requestId'] + ':execute'
        try:
            check = app.operations.requests.read(sid, check_id)
        except ValueError:
            check = {}
        verified_check = (environments.confirmed_result(row, check)
                          and check.get('action') == 'workspace.environments.execute')
        result = check.get('result', {}) if verified_check else {}
        guest = result.get('job')
        # New durable checks require their own terminal job; a removed guest
        # cannot turn a missing/uncertain command result into success.
        if job.get('durableVersion') == 1:
            verified_check = (verified_check and isinstance(guest, dict)
                              and guest.get('id') == check_id and guest.get('terminal') is True)
        state = {'completed': 'passed', 'failed': 'failed'}.get(check.get('state'), 'unknown') if verified_check else 'unknown'
        if verified_check and job.get('durableVersion') == 1:
            # Terminal cancelled/timed-out guest work is never a passing check.
            state = {'passed': 'passed', 'failed': 'failed', 'cancelled': 'cancelled',
                     'timed_out': 'failed'}.get(guest.get('state'), 'unknown')
            if state == 'passed' and (check.get('state') != 'completed' or result.get('ok') is not True):
                state = 'unknown'
        elif state == 'passed':
            execution = result.get('result')
            if (result.get('ok') is not True or not isinstance(execution, dict)
                    or type(execution.get('exit_code')) is not int or execution['exit_code'] != 0):
                state = 'unknown'
        if (environments.get(app.data_dir, sid, rid) != row
                or app.operations.requests.read(sid, cleanup['requestId']) != cleanup
                or check and app.operations.requests.read(sid, check_id) != check):
            return None
        retention = cleanup['result'].get('retention')
        return {'status': 'recovered', 'outcome': state, 'qualified': False,
                'cancelRequested': bool(job.get('cancelRequested')),
                'originalStatus': job['status'], 'resourceId': rid, 'resourceRevision': row['revision'],
                'cleanup': {'requestId': cleanup['requestId'], 'state': 'completed', 'environmentState': 'removed'},
                'check': {'requestId': check_id, 'state': check.get('state', 'unrecorded') if verified_check else 'unknown'},
                'retentionVerified': isinstance(retention, dict) and retention.get('verified') is True,
                'message': 'Later exact cleanup confirms this environment was removed. The original outcome remains retained; no check or cleanup was repeated.'}
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error):
        return None
