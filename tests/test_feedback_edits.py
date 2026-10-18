"""Owned, durable read reviews and exact append-only correction acceptance."""
import copy
import json

import pytest

from amplifier_web.feedback_edits import receipt
from amplifier_web.service import AppService, AppError
from test_feedback_followup import FEEDBACK_ID, api, seed, settle


async def prepare(app):
    app.clients.attach('review-owner')
    app.clients.attach('other-client')
    await seed(app)
    await app.dispatch('feedback.get', {'requestId': 'baseline-read', 'feedbackId': FEEDBACK_ID})
    await settle(app)


def proposal(**changes):
    return {'requestId': 'review-intent', 'feedbackId': FEEDBACK_ID, 'readRequestId': 'baseline-read',
            'title': 'Private proposed title', 'body': 'Private proposed body', **changes}


async def review(app, **changes):
    with app.clients.bind('review-owner'):
        await app.dispatch('feedback.edits.review', proposal(**changes))
        await settle(app)
    return receipt(app.feedback.followups, changes.get('requestId', 'review-intent'))


def correction(row, **changes):
    return {'requestId': 'review-correction', 'feedbackId': FEEDBACK_ID, 'reviewRequestId': row['requestId'],
            'title': row['review']['proposed']['title'], 'body': row['review']['proposed']['body'],
            'expectedRevision': row['review']['latest']['revision'], **changes}


async def test_review_reads_original_previous_latest_and_proposal_without_writes(tmp_path, api):
    app = AppService(tmp_path, workspace=tmp_path)
    try:
        await prepare(app)
        previous = copy.deepcopy(api.issue)
        api.issue['body'] += '\nMaintainer changed the report'
        row = await review(app)
        view = row['review']
        assert row['status'] == 'completed'
        assert view['original'] == {'title': 'Original report', 'body': 'Original body'}
        assert view['previous']['body'] == previous['body']
        assert view['latest']['body'] == api.issue['body']
        assert view['proposed']['body'] == 'Private proposed body'
        assert view['changedSinceRead'] and view['changedFields'] == ['body']
        assert view['automaticReplacement'] is False
        assert all(call.args[1] is None for call in api.call_args_list)
        before = api.call_count
        await review(app)
        assert api.call_count == before
        for owner in ('review-owner', 'other-client'):
            with app.clients.bind(owner):
                app.state['view']['feedbackCorrectionDraft'] = {'reviewRequestId': row['requestId']}
                projected = app.feedback.followups.project(app.state['feedback'])
                assert ('editReview' in projected) == (owner == 'review-owner')
                assert all(r['action'] != 'feedback.edits.review' for r in projected['followups'])
        assert 'Private proposed body' not in json.dumps(app.state['feedback'])
    finally:
        await app.close()


@pytest.mark.parametrize('change', [{'title': 'Not reviewed'}, {'body': 'Not reviewed'}, {'expectedRevision': 'a'*64}, {'reviewRequestId': 'missing-review'}])
async def test_reviewed_correction_refuses_changed_payload(tmp_path, api, change):
    app = AppService(tmp_path, workspace=tmp_path)
    try:
        await prepare(app)
        row = await review(app)
        with app.clients.bind('review-owner'), pytest.raises(AppError, match='Review these exact'):
            await app.dispatch('feedback.update', correction(row, **change))
        assert all(call.args[1] is None for call in api.call_args_list)
    finally:
        await app.close()


async def test_other_client_cannot_use_review_or_take_request_identity(tmp_path, api):
    app = AppService(tmp_path, workspace=tmp_path)
    try:
        await prepare(app)
        row = await review(app)
        with app.clients.bind('other-client'):
            with pytest.raises(AppError, match='different contents'):
                await app.dispatch('feedback.edits.review', proposal())
            with pytest.raises(AppError, match='Review these exact'):
                await app.dispatch('feedback.update', correction(row))
        with pytest.raises(AppError, match='attached client'):
            await app.dispatch('feedback.edits.review', proposal(requestId='unattached-review'))
        assert all(call.args[1] is None for call in api.call_args_list)
    finally:
        await app.close()


async def test_later_change_refuses_correction_and_keeps_original(tmp_path, api):
    app = AppService(tmp_path, workspace=tmp_path)
    try:
        await prepare(app)
        row = await review(app)
        api.issue['title'] = 'Maintainer updated title'
        with app.clients.bind('review-owner'):
            await app.dispatch('feedback.update', correction(row))
            await settle(app)
        stored = receipt(app.feedback.followups, 'review-correction')
        assert stored['code'] == 'feedback_revision_conflict'
        assert api.issue['title'] == 'Maintainer updated title'
        assert all(call.args[1] is None for call in api.call_args_list)
    finally:
        await app.close()


@pytest.mark.parametrize('lost_response', [False, True])
async def test_append_only_race_receipt_restart_and_consumed_review_never_replay(tmp_path, api, lost_response):
    app = AppService(tmp_path, workspace=tmp_path)
    original_response = api.side_effect
    async def response(endpoint, payload):
        if payload is not None:
            assert endpoint.endswith('/comments')
            # Change between final GET and POST: never replace this text.
            api.issue['body'] += '\nMaintainer concurrent edit'
            if lost_response:
                raise TimeoutError('private exception')
        return copy.deepcopy(await original_response(endpoint, payload))
    api.side_effect = response
    try:
        await prepare(app)
        row = await review(app)
        args = correction(row)
        with app.clients.bind('review-owner'):
            await app.dispatch('feedback.update', args)
            await settle(app)
        assert receipt(app.feedback.followups, args['requestId'])['status'] == ('unknown' if lost_response else 'submitted')
        assert receipt(app.feedback.followups, row['requestId'])['consumedBy'] == args['requestId']
        assert api.issue['body'].endswith('Maintainer concurrent edit')
    finally:
        await app.close()
    app = AppService(tmp_path, workspace=tmp_path)
    try:
        before = api.call_count
        with app.clients.bind('review-owner'):
            await app.dispatch('feedback.update', args)
            await settle(app)
            with pytest.raises(AppError, match='Review these exact'):
                await app.dispatch('feedback.update', {**args, 'requestId': 'duplicate-new-id'})
        assert api.call_count == before
        assert len([call for call in api.call_args_list if call.args[1] is not None]) == 1
    finally:
        await app.close()


async def test_review_checks_owner_again_and_requires_completed_matching_baseline(tmp_path, api):
    app = AppService(tmp_path, workspace=tmp_path)
    try:
        await prepare(app)
        with app.clients.bind('review-owner'), pytest.raises(AppError, match='Refresh this report'):
            await app.dispatch('feedback.edits.review', proposal(readRequestId='missing-baseline'))
        api.issue['user']['id'] = 8
        row = await review(app)
        assert row['status'] == 'failed' and row['code'] == 'feedback_owner_mismatch'
        assert 'review' not in row
        assert all(call.args[1] is None for call in api.call_args_list)
    finally:
        await app.close()
