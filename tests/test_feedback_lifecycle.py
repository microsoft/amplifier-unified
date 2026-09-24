import asyncio
import copy
import json
from unittest.mock import AsyncMock

import pytest

from amplifier_web import feedback
from amplifier_web.feedback_lifecycle import revision
from amplifier_web.service import AppService, AppError
from test_feedback_followup import FEEDBACK_ID, URL, api, seed, settle


async def unknown(app):
    await seed(app)
    await app.feedback.update(FEEDBACK_ID, status='unknown', url=None)


def search_api(api, *, matches=1, incomplete=False, owner=7):
    original = api.side_effect
    api.issue['user']['id'] = owner
    async def response(endpoint, payload):
        if endpoint.startswith('search/issues?'):
            items = [copy.deepcopy(api.issue) for _ in range(matches)] if 'microsoft' in endpoint else []
            for index, item in enumerate(items):
                item.update(number=42+index, html_url=feedback.ISSUES_URL+f'/{42+index}')
            return {'items': items, 'total_count': len(items), 'incomplete_results': incomplete}
        if endpoint.endswith('/43'):
            return {**api.issue, 'number': 43, 'html_url': feedback.ISSUES_URL+'/43'}
        return await original(endpoint, payload)
    api.side_effect = response


async def test_reconcile_binds_unique_owner_marker_without_writes_and_survives_restart(tmp_path, api):
    search_api(api)
    app = AppService(tmp_path, workspace=tmp_path)
    args = {'feedbackId': FEEDBACK_ID, 'requestId': 'reconcile-unique'}
    try:
        await unknown(app)
        await app.dispatch('session.create', {})
        await app.app_bridge('dispatch', {'action': 'feedback.reconcile', 'args': args}, app._session()['id'])
        await settle(app)
        receipt = app.state['feedback']['followups'][0]
        assert receipt['outcome'] == 'found' and receipt['url'] == URL
        assert app.state['feedback']['requests'][0]['status'] == 'submitted'
        assert all(call.args[1] is None for call in api.call_args_list)
        before = api.call_count
        await app.dispatch('feedback.reconcile', args)
        await settle(app)
        assert api.call_count == before
    finally:
        await app.close()
    app = AppService(tmp_path, workspace=tmp_path)
    try:
        assert app.feedback.followups.target(FEEDBACK_ID)[0] == URL
        await app.dispatch('feedback.get', {'feedbackId': FEEDBACK_ID, 'requestId': 'read-reconciled'})
        await settle(app)
        assert app.state['feedback']['report']['revision'] == revision(api.issue)
    finally:
        await app.close()


@pytest.mark.parametrize('matches,incomplete,owner,outcome', [(0,False,7,'not_found'),(2,False,7,'indeterminate'),(1,True,7,'indeterminate'),(1,False,8,'not_found')])
async def test_unproven_reconciliation_preserves_unknown_and_never_reposts(tmp_path, api, matches, incomplete, owner, outcome):
    search_api(api, matches=matches, incomplete=incomplete, owner=owner)
    app = AppService(tmp_path, workspace=tmp_path)
    try:
        await unknown(app)
        await app.dispatch('feedback.reconcile', {'feedbackId': FEEDBACK_ID, 'requestId': 'reconcile-unproven'})
        await settle(app)
        assert app.state['feedback']['followups'][0]['outcome'] == outcome
        assert app.state['feedback']['requests'][0]['status'] == 'unknown'
        assert all(call.args[1] is None for call in api.call_args_list)
        with pytest.raises(AppError):
            await app.dispatch('feedback.comment', {'feedbackId': FEEDBACK_ID, 'requestId': 'no-unverified-post', 'body': 'No'})
    finally:
        await app.close()


async def test_correction_preserves_remote_issue_and_audits_revision_idempotently(tmp_path, api):
    app = AppService(tmp_path, workspace=tmp_path)
    args = {'feedbackId': FEEDBACK_ID, 'requestId': 'correction-version-1', 'expectedRevision': revision(api.issue), 'title': 'Corrected title', 'body': 'Corrected description'}
    before = copy.deepcopy(api.issue)
    try:
        await seed(app)
        await app.dispatch('feedback.update', args)
        await settle(app)
        row = app.state['feedback']['followups'][0]
        assert row['status'] == 'submitted' and 'audit' not in row
        stored = json.loads(app.db.execute('SELECT receipt FROM feedback_followups WHERE id=?', (args['requestId'],)).fetchone()[0])
        assert stored['audit']['priorBody'] == before['body']
        assert stored['audit']['sourceRevision'] == args['expectedRevision']
        posts = [c for c in api.call_args_list if c.args[1] is not None]
        assert len(posts) == 1 and posts[0].args[0].endswith('/comments')
        assert 'Corrected description' in posts[0].args[1]['body']
        assert api.issue == before
        await app.dispatch('feedback.update', args)
        await settle(app)
        assert len([c for c in api.call_args_list if c.args[1] is not None]) == 1
        await app.dispatch('feedback.get', {'feedbackId': FEEDBACK_ID, 'requestId': 'read-corrected'})
        await settle(app)
        assert app.state['feedback']['report']['editableBody'] == args['body']
    finally:
        await app.close()


async def test_stale_revision_rejects_before_post_and_uncertain_correction_never_replays(tmp_path, api):
    app = AppService(tmp_path, workspace=tmp_path)
    args = {'feedbackId': FEEDBACK_ID, 'requestId': 'correction-conflict', 'expectedRevision': revision(api.issue), 'title': 'T', 'body': 'B'}
    try:
        await seed(app)
        api.issue['body'] += '\nMaintainer update'
        await app.dispatch('feedback.update', args)
        await settle(app)
        assert app.state['feedback']['followups'][0]['code'] == 'feedback_revision_conflict'
        assert all(c.args[1] is None for c in api.call_args_list)
        original = api.side_effect
        async def lose(endpoint, payload):
            if payload is not None:
                raise TimeoutError('private-fixture')
            return await original(endpoint, payload)
        api.side_effect = lose
        args.update(requestId='correction-unknown', expectedRevision=revision(api.issue))
        await app.dispatch('feedback.update', args)
        await settle(app)
        assert app.state['feedback']['followups'][0]['status'] == 'unknown'
        await app.dispatch('feedback.update', args)
        await settle(app)
        assert len([c for c in api.call_args_list if c.args[1] is not None]) == 1
        assert 'private-fixture' not in json.dumps(app.state)
    finally:
        await app.close()

@pytest.mark.parametrize('action,desired', [('feedback.close','closed'),('feedback.reopen','open')])
async def test_state_change_preserves_text_and_retries_are_at_most_once(tmp_path, api, action, desired):
    app=AppService(tmp_path,workspace=tmp_path)
    api.issue['state']='open' if desired=='closed' else 'closed'
    original=api.side_effect
    async def response(endpoint,payload,*,method=None):
        if method=='PATCH':
            assert payload=={'state':desired}
            api.issue.update(payload)
            return copy.deepcopy(api.issue)
        return await original(endpoint,payload)
    api.side_effect=response
    args={'feedbackId':FEEDBACK_ID,'requestId':'state-change-once','expectedRevision':revision(api.issue)}
    try:
        await seed(app)
        prior=api.issue['body']
        await app.dispatch(action,args);await settle(app)
        assert app.state['feedback']['followups'][0]['status']=='submitted'
        assert api.issue['body']==prior and api.issue['state']==desired
        before=api.call_count
        await app.dispatch(action,args);await settle(app)
        assert api.call_count==before
        assert sum(call.kwargs.get('method')=='PATCH' for call in api.call_args_list)==1
    finally:await app.close()

async def test_uncertain_close_is_durable_and_not_repeated(tmp_path,api):
    app=AppService(tmp_path,workspace=tmp_path)
    api.issue['state']='open'
    original=api.side_effect
    async def response(endpoint,payload,*,method=None):
        if method=='PATCH':raise TimeoutError('sensitive-fixture')
        return await original(endpoint,payload)
    api.side_effect=response
    args={'feedbackId':FEEDBACK_ID,'requestId':'state-uncertain','expectedRevision':revision(api.issue)}
    try:
        await seed(app)
        await app.dispatch('feedback.close',args);await settle(app)
        assert app.state['feedback']['followups'][0]['status']=='unknown'
    finally:await app.close()
    app=AppService(tmp_path,workspace=tmp_path)
    try:
        before=api.call_count
        await app.dispatch('feedback.close',args);await settle(app)
        assert api.call_count==before
        assert 'sensitive-fixture' not in json.dumps(app.state)
    finally:await app.close()
