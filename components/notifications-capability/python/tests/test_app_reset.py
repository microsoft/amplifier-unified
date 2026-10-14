import json,pytest
from amplifier_unified_notifications.owner import Owner
C={'fenceId':'f','commandId':'hold','purpose':'recovery','instanceId':'i','dataScope':'d'}
async def call(owner,operation,args,context=C):return await owner.request('appReset',{'operation':operation,'args':args,'context':context})
async def prepared(owner,command='prepare',parts=None,**extra):
    return await call(owner,'prepare',{'commandId':command,'parts':parts or ['notifications.settings','notifications.credentials'],'privateContentReviewed':True,'credentialsReviewed':True,**extra})
@pytest.mark.asyncio
async def test_private_reset_exact_hold_preserves_commands_deliveries_and_undo(tmp_path):
    owner=Owner({'stateDirectory':str(tmp_path)});owner.save({'expectedRevision':0,'patch':{'topic':'private-topic','token':'private-token','enabled':True,'preview':True}},'save')
    commands=list(owner.db.execute('SELECT * FROM commands'));deliveries=list(owner.db.execute('SELECT * FROM deliveries'))
    try:
        with pytest.raises(ValueError):await prepared(owner)
        assert owner.intake.acquire(C)['acquired']
        with pytest.raises(ValueError):await call(owner,'prepare',{'commandId':'wrong','parts':['notifications.settings'],'privateContentReviewed':True},{**C,'commandId':'other'})
        review=await prepared(owner);assert review['receipt']['state']=='succeeded';assert 'private-topic' not in json.dumps(review) and 'private-token' not in json.dumps(review)
        args={'commandId':'reset','preparedId':review['preparedId'],'reviewHash':review['reviewHash']};result=await call(owner,'apply',args);assert result['receipt']['state']=='succeeded';assert owner.private()[1]['token']=='' and not owner.public()['enabled']
        assert (await call(owner,'apply',args))==result
        assert list(owner.db.execute('SELECT * FROM commands'))==commands and list(owner.db.execute('SELECT * FROM deliveries'))==deliveries
        undo=await prepared(owner,'undo-review',restoreCommandId='reset');restored=await call(owner,'restore',{'commandId':'undo','preparedId':undo['preparedId'],'reviewHash':undo['reviewHash'],'resetCommandId':'reset','expectedPostResetRevision':result['postResetRevision']});assert restored['restored']
        assert owner.private()[1]['token']=='private-token' and owner.public()['enabled']
        assert 'private-token' not in json.dumps(await call(owner,'inspect',{'commandId':'undo'}))
    finally:await owner.close()
@pytest.mark.asyncio
async def test_settings_only_never_retains_or_changes_credentials_stale_review_and_undo(tmp_path):
    owner=Owner({'stateDirectory':str(tmp_path)});owner.save({'expectedRevision':0,'patch':{'topic':'secret-topic','token':'secret-token'}},'seed')
    try:
        owner.intake.acquire(C);review=await prepared(owner,parts=['notifications.settings']);private=owner.db.execute('SELECT private FROM app_reset_previews WHERE id=?',(review['preparedId'],)).fetchone()[0];assert 'secret-token' not in private and 'secret-topic' not in private
        owner.save({'expectedRevision':1,'patch':{'preview':True}},'concurrent')
        stale=await call(owner,'apply',{'commandId':'stale','preparedId':review['preparedId'],'reviewHash':review['reviewHash']});assert stale['receipt']['state']=='refused'
        review=await prepared(owner,'fresh',parts=['notifications.settings']);reset=await call(owner,'apply',{'commandId':'reset','preparedId':review['preparedId'],'reviewHash':review['reviewHash']});assert owner.private()[1]['token']=='secret-token'
        undo=await prepared(owner,'undo-review',parts=['notifications.settings'],restoreCommandId='reset');owner.save({'expectedRevision':int(reset['postResetRevision']),'patch':{'server':'https://other.example'}},'newer')
        reject=await call(owner,'restore',{'commandId':'undo','preparedId':undo['preparedId'],'reviewHash':undo['reviewHash'],'resetCommandId':'reset','expectedPostResetRevision':reset['postResetRevision']});assert reject['receipt']['state']=='refused';assert owner.public()['server']=='https://other.example'
    finally:await owner.close()
@pytest.mark.asyncio
async def test_restart_exact_receipt_and_unknown_never_replayed(tmp_path):
    owner=Owner({'stateDirectory':str(tmp_path)});owner.intake.acquire(C);review=await prepared(owner,parts=['notifications.settings']);args={'commandId':'reset','preparedId':review['preparedId'],'reviewHash':review['reviewHash']};original=await call(owner,'apply',args);await owner.close()
    owner=Owner({'stateDirectory':str(tmp_path)})
    try:
        assert (await call(owner,'inspect',{'commandId':'reset'}))['receipt']==original['receipt']
        assert (await call(owner,'apply',args))==original
        unknown={'ownerId':'notifications','commandId':'lost','operation':'apply','state':'unknown','replayed':False}
        with owner.db:owner.db.execute('INSERT INTO app_reset_commands VALUES(?,?,?)',('lost','unused',json.dumps(unknown)))
        assert (await call(owner,'inspect',{'commandId':'lost'}))['receipt']['state']=='unknown'
        review=await prepared(owner,'blocked',parts=['notifications.settings']);assert review['receipt']['state']=='refused'
    finally:await owner.close()
