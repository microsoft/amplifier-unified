"""Private journal crash boundaries, without uploads, network, or replay."""
import sqlite3
import pytest
pytestmark = pytest.mark.asyncio
from amplifier_unified_feedback.owner import Owner

C = dict(fenceId='aggregate-fence', commandId='aggregate-command', purpose='distribution-update', instanceId='launch', dataScope='scope')
ID = 'feedback'
CHILDREN = [ID + ':uploads', ID + ':python']
METHOD = 'quiescence.aggregateAdmission'

async def forbidden(*args):
    raise AssertionError('No external work or upload is permitted')

async def notify(*args):
    pass

def make(path):
    return Owner({'dataDir': str(path)}, forbidden, notify, github=forbidden)

def args(operation, **fields):
    return dict(operation=operation, context=C, ownerId=ID, **fields)

def proof():
    return dict(C, kind='distribution-admission-abort', verified=True, receiptId='abort-proof')

def receipt(child, status):
    return {**{k: C[k] for k in ('commandId', 'fenceId', 'instanceId', 'dataScope')}, 'ownerId': child, 'status': status, 'receiptId': child + '-receipt'}

async def begin(owner):
    return await owner.request(METHOD, args('begin', owners=CHILDREN))

async def test_lost_dispatch_reply_is_pending_across_restart_and_needs_real_receipt(tmp_path):
    owner = make(tmp_path)
    try:
        assert (await owner.request('initialize', {}))['quiescence']['aggregateAdmission'] == {'version': 1}
        await begin(owner)
        pending = await owner.request(METHOD, args('attempt', childOwnerId=CHILDREN[0]))
        assert pending['attempts'] == [{'childOwnerId': CHILDREN[0], 'status': 'pending'}]
    finally:
        await owner.close()
    owner = make(tmp_path)
    try:
        assert await owner.request(METHOD, args('read')) == pending
        assert await owner.request(METHOD, args('attempt', childOwnerId=CHILDREN[0])) == pending
        with pytest.raises(ValueError):
            await owner.request(METHOD, args('attempt', childOwnerId=CHILDREN[1]))
        await owner.request(METHOD, args('abortIntent', proof=proof()))
        with pytest.raises(ValueError, match='unsettled'):
            await owner.request(METHOD, args('complete'))
        await owner.request(METHOD, args('abortReceipt', childOwnerId=CHILDREN[0], receipt=receipt(CHILDREN[0], 'released')))
        result = await owner.request(METHOD, args('complete'))
        assert result['status'] == 'released' and set(result) == set(receipt(ID, 'released'))
    finally:
        await owner.close()
    owner = make(tmp_path)
    try:
        assert await owner.request(METHOD, args('complete')) == result
        with pytest.raises(ValueError):
            await owner.request(METHOD, args('abortIntent', proof={**proof(), 'receiptId': 'changed'}))
    finally:
        await owner.close()

async def test_acquired_and_refused_children_settle_reverse_order_with_exact_results(tmp_path):
    owner = make(tmp_path)
    try:
        await begin(owner)
        await owner.request(METHOD, args('attempt', childOwnerId=CHILDREN[0]))
        acquired = {'acquired': True, 'intakeClosed': True, 'fenceId': C['fenceId']}
        await owner.request(METHOD, args('result', childOwnerId=CHILDREN[0], acquisition=acquired))
        await owner.request(METHOD, args('attempt', childOwnerId=CHILDREN[1]))
        refused = {'acquired': False, 'executed': False, 'reason': 'busy'}
        await owner.request(METHOD, args('result', childOwnerId=CHILDREN[1], acquisition=refused))
        with pytest.raises(ValueError):
            await owner.request(METHOD, args('result', childOwnerId=CHILDREN[1], acquisition={**refused, 'reason': 'different'}))
        await owner.request(METHOD, args('abortIntent', proof=proof()))
        with pytest.raises(ValueError, match='reverse'):
            await owner.request(METHOD, args('abortReceipt', childOwnerId=CHILDREN[0], receipt=receipt(CHILDREN[0], 'released')))
        with pytest.raises(ValueError, match='contradicts'):
            await owner.request(METHOD, args('abortReceipt', childOwnerId=CHILDREN[1], receipt=receipt(CHILDREN[1], 'released')))
        await owner.request(METHOD, args('abortReceipt', childOwnerId=CHILDREN[1], receipt=receipt(CHILDREN[1], 'not-acquired')))
        await owner.request(METHOD, args('abortReceipt', childOwnerId=CHILDREN[0], receipt=receipt(CHILDREN[0], 'released')))
        owner.intake.calls = 1
        with pytest.raises(ValueError, match='in flight'):
            await owner.request(METHOD, args('complete'))
        owner.intake.calls = 0
        assert (await owner.request(METHOD, args('complete')))['status'] == 'released'
        assert (await owner.request('snapshot', {}))['items'] == []
    finally:
        owner.intake.calls = 0
        await owner.close()

async def test_original_empty_begin_can_settle_without_inventing_child_attempts(tmp_path):
    owner = make(tmp_path)
    try:
        assert await owner.request(METHOD, args('read')) is None
        with pytest.raises(ValueError, match='original'):
            await owner.request(METHOD, args('complete'))
        await begin(owner)
        await owner.request(METHOD, args('abortIntent', proof=proof()))
        result = await owner.request(METHOD, args('complete'))
        assert result['status'] == 'not-acquired'
        assert (await owner.request(METHOD, args('read')))['attempts'] == []
    finally:
        await owner.close()

@pytest.mark.parametrize('damage', ['table', 'marker'])
async def test_lost_private_profile_refuses_startup_without_repair(tmp_path, damage):
    owner = make(tmp_path)
    await begin(owner)
    await owner.close()
    db = sqlite3.connect(tmp_path / 'intake.sqlite3')
    with db:
        if damage == 'table':
            db.execute('DROP TABLE feedback_aggregate_admissions')
        else:
            db.execute("DELETE FROM releases WHERE fence='feedback-aggregate-admission-profile-v1'")
    db.close()
    with pytest.raises(ValueError, match='profile unavailable'):
        make(tmp_path)
    db = sqlite3.connect((tmp_path / 'intake.sqlite3').as_uri() + '?mode=ro', uri=True)
    try:
        table = db.execute("SELECT name FROM sqlite_master WHERE name='feedback_aggregate_admissions'").fetchone()
        marker = db.execute("SELECT value FROM releases WHERE fence='feedback-aggregate-admission-profile-v1'").fetchone()
        assert (table is None) == (damage == 'table')
        assert (marker is None) == (damage == 'marker')
    finally:
        db.close()
