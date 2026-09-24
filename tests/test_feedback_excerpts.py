import base64
import copy
import json
from unittest.mock import AsyncMock

import pytest

from amplifier_web import feedback, feedback_attachments
from amplifier_web.feedback_excerpts import redact
from amplifier_web.service import AppError, AppService
from test_feedback_attachments import settle, submission


@pytest.fixture
def github(monkeypatch):
    target = {'full_name': feedback.REPOSITORY, 'private': True}
    writes = []
    async def api(endpoint, payload):
        if payload is None:
            return copy.deepcopy(target)
        writes.append((endpoint, payload))
        return {'object': {'sha': 'a'*40}} if endpoint.endswith('/git/refs') else {'sha': 'a'*40}
    issue = AsyncMock(return_value=feedback.ISSUES_URL+'/999')
    monkeypatch.setattr(feedback, 'github_api', api)
    monkeypatch.setattr(feedback, 'create_issue', issue)
    monkeypatch.setattr(feedback.shutil, 'which', lambda _: '/fixture/gh')
    return target, writes, issue


async def preview(app, **options):
    if not app.state.get('selectedSessionId'):
        await app.dispatch('session.create', {'title': 'PRIVATE TITLE'})
        app._session()['messages'] = [{'id':'one','role':'user','text':'What happened?'},
            {'id':'two','role':'assistant','text':'Expected response'}]
    sid = app._session()['id']
    exported = await app.dispatch('session.export', {'id':sid, 'format':'markdown','destination':'none',
        'scope':'range','minimal':True,'fromMessageId':'one','throughMessageId':'two'})
    result = await app.dispatch('feedback.excerpt.review', {'id':sid,'snapshotId':exported['result']['snapshotId'],**options})
    return result['result']


async def stage(app, review, **options):
    args = {'id':review['sessionId'],'requestId':'stage-excerpt-once','reviewId':review['reviewId'],
            'acknowledgeDisclosure':True,'acknowledgeWarnings':True,**options}
    return (await app.dispatch('feedback.excerpt.stage', args))['result']


def consent(*rows):
    return {'confirmExcerpts': True, 'confirmedExcerpts': [
        {'id': row['id'], 'sha256': row['excerpt']['sha256']} for row in rows]}


def test_redaction_reports_counts_without_retaining_sensitive_values(monkeypatch):
    monkeypatch.setenv('TEST_API_KEY', 'fixture-top-secret')
    text = 'Normal code\nBearer abcdefghij api_key="hidden" fixture-top-secret\n/user@example.com /home/person/private.txt C:\\Users\\name\\private.txt https://private.example/path?token=foo'
    cleaned, changes, warnings = redact(text)
    assert 'Normal code' in cleaned
    for value in ('abcdefghij','hidden','fixture-top-secret','user@example.com','/home/person','C:\\Users','private.example'):
        assert value not in cleaned and value not in json.dumps(changes)
    assert changes and 'incomplete' in warnings[0]


async def test_review_is_local_edited_exact_snapshot_and_private_by_verified_source(tmp_path, github):
    target, writes, issue = github
    app = AppService(tmp_path, workspace=tmp_path)
    try:
        review = await preview(app)
        assert review['visibility']=='private' and 'PRIVATE TITLE' not in review['text']
        assert review['summary']['messageCount']==2 and writes==[] and issue.await_count==0
        assert review['text'] not in json.dumps(app.browser_state())
        updated = await preview(app, text='Only the essential reproduction steps.\n')
        assert updated['reviewId'] != review['reviewId']
        row = await stage(app, updated)
        stored = json.loads(app.db.execute('SELECT metadata FROM feedback_attachments').fetchone()[0])
        assert feedback_attachments.read_verified(tmp_path, stored).decode() == updated['text']
        assert row['excerpt']['reviewId']==updated['reviewId']
        app._session()['messages'].append({'id':'later','role':'user','text':'Later secret'})
        await stage(app, updated)
        assert len(app.state['view']['feedbackDraft']['attachments'])==1 and writes==[]
        with pytest.raises(AppError, match='different review'):
            await stage(app, review)
        with pytest.raises(AppError):
            await app.dispatch('feedback.submit', submission(attachmentIds=[row['id']]))
        assert writes==[]
        await app.dispatch('feedback.submit', submission(attachmentIds=[row['id']],**consent(row)))
        await settle(app)
        assert app.state['feedback']['requests'][0]['status']=='submitted'
        blobs=[payload for endpoint,payload in writes if endpoint.endswith('/git/blobs')]
        assert len(blobs)==1 and base64.b64decode(blobs[0]['content']).decode()==updated['text']
        assert 'Later secret' not in str(writes)
    finally:
        await app.close()


@pytest.mark.parametrize('private', [True, False])
async def test_exact_visibility_ack_and_recheck_before_any_write(tmp_path, github, private):
    target, writes, issue = github
    target['private']=private
    app=AppService(tmp_path, workspace=tmp_path)
    try:
        review=await preview(app, text='api_key=secret-value')
        assert review['visibility']==('private' if private else 'public')
        assert review['requiresExtraReview']
        with pytest.raises(AppError, match='acknowledge'):
            await stage(app, review, acknowledgeWarnings=False)
        row=await stage(app,review)
        target['private']=not private
        await app.dispatch('feedback.submit', submission(attachmentIds=[row['id']],**consent(row)))
        await settle(app)
        assert app.state['feedback']['requests'][0]['status']=='failed' and writes==[] and issue.await_count==0
    finally:
        await app.close()


async def test_explicitly_reviewed_public_excerpt_uploads_once_and_reports_visibility(tmp_path, github):
    target,writes,issue=github;target['private']=False
    app=AppService(tmp_path, workspace=tmp_path)
    try:
        review=await preview(app);row=await stage(app,review)
        args=submission(attachmentIds=[row['id']],**consent(row))
        await app.dispatch('feedback.submit',args);await settle(app)
        assert app.state['feedback']['requests'][0]['attachments'][0]['visibility']=='public'
        assert 'publicly visible' in issue.call_args.args[1]
        count=len(writes)
        await app.dispatch('feedback.submit',args);await settle(app)
        assert len(writes)==count and issue.await_count==1
    finally:
        await app.close()


async def test_foreign_session_review_denied_and_removed_stage_not_resurrected(tmp_path, github):
    app=AppService(tmp_path, workspace=tmp_path)
    try:
        review=await preview(app);sid=review['sessionId']
        await app.dispatch('session.create',{'title':'Other'})
        other=app._session()['id']
        with pytest.raises(AppError,match='calling conversation'):
            await app.app_bridge('dispatch',{'action':'feedback.excerpt.review','args':{'id':sid,'snapshotId':review['snapshotId']}},other)
        with pytest.raises(AppError,match='different conversation'):
            await stage(app,review,id=other)
        with pytest.raises(AppError,match='selected conversation changed'):
            await stage(app,review)
        await app.dispatch('session.select', {'id':sid})
        row=await stage(app,review)
        await app.dispatch('feedback.attachment.remove',{'id':row['id']})
        await stage(app,review)
        assert not app.state['view']['feedbackDraft']['attachments']
    finally:
        await app.close()


async def test_unknown_destination_refuses_preview_and_large_text_is_not_truncated(tmp_path, github):
    target,writes,issue=github
    app=AppService(tmp_path,workspace=tmp_path)
    try:
        with pytest.raises(AppError,match='smaller excerpt'):
            await preview(app,text='x'*64001)
        target.pop('private')
        with pytest.raises(AppError,match='visibility'):
            await preview(app)
        assert writes==[]
    finally:
        await app.close()


async def test_agent_review_and_stage_share_user_path_with_explicit_browser_target(tmp_path, github):
    app = AppService(tmp_path, workspace=tmp_path)
    try:
        review = await preview(app)
        sid = review['sessionId']
        response = await app.app_bridge('dispatch', {'action':'feedback.excerpt.review',
            'args':{'snapshotId':review['snapshotId']}}, sid)
        assert response['result']['sha256'] == review['sha256']
        app.clients.attach('excerpt-browser')
        with app.clients.bind('excerpt-browser'):
            await app.dispatch('session.select', {'id':sid})
            queue = app.subscribe()
        try:
            result = await app.app_bridge('dispatch', {'action':'feedback.excerpt.stage',
                'args':{'clientId':'excerpt-browser','reviewId':review['reviewId'],
                        'requestId':'agent-excerpt-stage','acknowledgeDisclosure':True}}, sid)
            assert result['result']['excerpt']['reviewId'] == review['reviewId']
            assert github[1] == []
        finally:
            app.unsubscribe(queue)
    finally:
        await app.close()


async def test_ui_owned_hidden_rows_do_not_enter_excerpt(tmp_path, github):
    app = AppService(tmp_path, workspace=tmp_path)
    try:
        await preview(app)
        app._session()['messages'].insert(1, {'id':'hidden','role':'assistant','text':'PRIVATE REASONING','ephemeral':True})
        review = await preview(app)
        assert 'PRIVATE REASONING' not in review['text'] and review['summary']['messageCount'] == 2
    finally:
        await app.close()


async def test_final_consent_matches_complete_server_excerpt_set_and_exact_retry_survives_changes(tmp_path, github):
    app = AppService(tmp_path, workspace=tmp_path)
    try:
        first = await stage(app, await preview(app, text='First explicitly reviewed excerpt'))
        stale = submission(attachmentIds=[first['id']], **consent(first))
        app.state['view']['feedbackDraft'].update(consent(first))
        # Same accepted staging retry neither duplicates nor revokes exact consent.
        await stage(app, await preview(app, text='First explicitly reviewed excerpt'))
        assert app.state['view']['feedbackDraft']['confirmExcerpts'] is True
        sid = app._session()['id']
        review = await preview(app, text='Second explicitly reviewed excerpt')
        app.clients.attach('consent-browser')
        with app.clients.bind('consent-browser'):
            await app.dispatch('session.select', {'id':sid})
            app.state['view']['feedbackDraft'] = copy.deepcopy(app._state['view']['feedbackDraft'])
            queue = app.subscribe()
        try:
            response = await app.app_bridge('dispatch', {'action':'feedback.excerpt.stage', 'args':{
                'clientId':'consent-browser','reviewId':review['reviewId'], 'requestId':'second-excerpt-stage',
                'acknowledgeDisclosure':True}}, sid)
            second = response['result']
            with app.clients.bind('consent-browser'):
                assert app.state['view']['feedbackDraft']['confirmExcerpts'] is False
                assert app.state['view']['feedbackDraft']['confirmedExcerpts'] == []
                for invalid in (stale,
                                {**stale, 'attachmentIds':[first['id'],second['id']]},
                                {**stale, 'confirmedExcerpts':[]},
                                {**stale, 'confirmedExcerpts':[{'id':first['id'],'sha256':'0'*64}]}):
                    with pytest.raises(AppError,match='exact files and hashes'):
                        await app.dispatch('feedback.submit', invalid)
                assert github[1] == [] and github[2].await_count == 0
                accepted = submission(attachmentIds=[first['id'],second['id']], **consent(first,second))
                await app.dispatch('feedback.submit', accepted)
                await settle(app)
                count = len(github[1])
                await app.dispatch('feedback.attachment.remove', {'id':second['id']})
                await app.dispatch('feedback.submit', accepted)
                await settle(app)
                assert len(github[1]) == count and github[2].await_count == 1
        finally:
            app.unsubscribe(queue)
    finally:
        await app.close()
