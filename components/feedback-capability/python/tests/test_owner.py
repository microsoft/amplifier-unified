import asyncio
import base64
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from amplifier_unified_feedback.owner import Owner, REPOSITORY, revision

class GitHub:
    def __init__(self):
        self.calls=[]; self.issues={}; self.comments=[]; self.private=True; self.lose=False; self.owner=7
    async def __call__(self, endpoint, payload, method=None):
        self.calls.append((endpoint,payload,method))
        if endpoint=='user':return {'id':self.owner,'login':'fixture-owner'}
        if endpoint=='repos/'+REPOSITORY:return {'full_name':REPOSITORY,'private':self.private}
        if endpoint.startswith('search/issues?'):
            values=list(self.issues.values()) if 'microsoft' in endpoint else []
            return {'items':values,'incomplete_results':False,'total_count':len(values)}
        if endpoint=='repos/'+REPOSITORY+'/issues' and payload:
            row={'id':1,'number':1,'html_url':'https://github.com/'+REPOSITORY+'/issues/1','state':'open','user':{'id':7},'updated_at':'2026-10-02T00:00:00Z',**payload}
            self.issues[1]=row
            if self.lose:raise OSError('lost acknowledged response')
            return row
        if '/git/' in endpoint:
            if endpoint.endswith('/refs'):return {'object':{'sha':'b'*40}}
            return {'sha':'b'*40}
        if endpoint.endswith('/comments') and payload:
            self.comments.append(payload)
            return {'id':9,'html_url':'https://github.com/'+REPOSITORY+'/issues/1#issuecomment-9','user':{'id':7}}
        if '/comments?' in endpoint:return []
        if endpoint=='repos/'+REPOSITORY+'/issues/1':
            if payload:self.issues[1].update(payload)
            return self.issues[1]
        raise AssertionError('Unexpected fixture endpoint: '+endpoint)

class OwnerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.github=GitHub(); self.data=b'original attachment'; self.changes=0; self.reads=[]
        async def host(method,args):
            self.reads.append((method,args))
            if method=='attachmentMetadata':return {'size':len(self.data),'sha256':hashlib.sha256(self.data).hexdigest(),'contentType':'text/plain'}
            if method=='attachmentPage':return {'data':base64.b64encode(self.data[args['offset']:args['offset']+args['limit']]).decode(),'encoding':'base64'}
            if method=='readExport':return {'text':'## User\n\nPreserve the history.\n','summary':{'minimal':True,'format':'markdown','scope':'range','messageCount':1}}
            raise AssertionError(method)
        async def notify(*args):self.changes+=1
        self.host,self.notify=host,notify
        self.owner=Owner({'dataDir':self.tmp.name},host,notify,github=self.github)
    async def asyncTearDown(self):
        await self.owner.close();self.tmp.cleanup()
    async def action(self,operation,args,**context):
        return await self.owner.request('action',{'operation':'feedback.'+operation,'args':args,**context})
    def submission(self,identity='original-request',**extra):return {'requestId':identity,'title':'Reviewed report','body':'Only this explicit text','category':'bug',**extra}

    async def test_lost_issue_response_restarts_and_reconciles_without_repost(self):
        self.github.lose=True
        args=self.submission()
        self.assertEqual((await self.action('submit',args))['status'],'unknown')
        await self.owner.close();self.owner=Owner({'dataDir':self.tmp.name},self.host,self.notify,github=self.github)
        self.assertEqual((await self.action('submit',args))['status'],'unknown')
        outcome=await self.action('reconcile',{'requestId':'read-original','feedbackId':args['requestId']})
        self.assertEqual(outcome['outcome'],'found')
        self.assertEqual(self.owner.receipt(args['requestId'])['status'],'completed')
        self.assertEqual(sum(bool(payload) and endpoint.endswith('/issues') for endpoint,payload,_ in self.github.calls),1)
        with self.assertRaisesRegex(ValueError,'different feedback'):
            await self.action('submit',{**args,'body':'different'})

    async def test_corrections_preserve_original_and_owner_revision_checks_refuse_writes(self):
        await self.action('submit',self.submission())
        read=await self.action('get',{'requestId':'read-current','feedbackId':'original-request'})
        report=read['report'];original=dict(self.github.issues[1])
        correction={'requestId':'correct-version','feedbackId':'original-request','title':'Corrected title','body':'A correction','expectedRevision':report['revision']}
        self.assertEqual((await self.action('update',correction))['status'],'completed')
        self.assertEqual(self.github.issues[1],original)
        self.assertIn('Original report retained',self.github.comments[0]['body'])
        refreshed=await self.action('get',{'requestId':'read-correction','feedbackId':'original-request'})
        self.assertEqual(refreshed['report']['editableTitle'],'Corrected title')
        self.github.issues[1]['title']='Maintainer change'
        refused=await self.action('close',{'requestId':'close-stale','feedbackId':'original-request','expectedRevision':report['revision']})
        self.assertEqual(refused['status'],'failed');self.assertEqual(self.github.issues[1]['state'],'open')
        self.github.owner=8
        refused=await self.action('comment',{'requestId':'wrong-owner','feedbackId':'original-request','body':'Do not post'})
        self.assertEqual(refused['status'],'failed');self.assertEqual(len(self.github.comments),1)

    async def test_local_staging_is_chunked_and_public_ordinary_files_never_upload(self):
        self.data=b'z'*600000
        result=await self.action('attachment.add',{'requestId':'stage-file','resourceUri':'amplifier-feedback-attachment://owned/body','name':'../safe.txt','sha256':hashlib.sha256(self.data).hexdigest()})
        attachment=result['attachment'];self.assertEqual(attachment['name'],'safe.txt')
        self.assertEqual(len([row for row in self.reads if row[0]=='attachmentPage']),3)
        self.assertEqual(self.github.calls,[])
        self.github.private=False
        result=await self.action('submit',self.submission(attachmentIds=[attachment['id']]))
        self.assertEqual(result['status'],'failed')
        self.assertFalse(any(payload is not None for _,payload,_ in self.github.calls))

    async def test_exact_excerpt_consent_visibility_change_and_independent_drafts(self):
        scope='ahp-session:/selected'
        reviewed=await self.action('excerpt.review',{'requestId':'review-text','sessionId':scope,'resourceUri':'amplifier-export:/snapshot','text':'Contact person@example.invalid about /private/path'})
        review=reviewed['review'];self.assertNotIn('person@example.invalid',review['text']);self.assertTrue(review['requiresExtraReview'])
        staged=await self.action('excerpt.stage',{'requestId':'stage-excerpt','sessionId':scope,'reviewId':review['reviewId'],'acknowledgeDisclosure':True,'acknowledgeWarnings':True})
        item=staged['attachment']
        refused=await self.action('submit',self.submission('missing-consent',attachmentIds=[item['id']]))
        self.assertEqual(refused['status'],'failed')
        self.github.private=False
        refused=await self.action('submit',self.submission('visibility-changed',attachmentIds=[item['id']],confirmExcerpts=True,confirmedExcerpts=[{'id':item['id'],'sha256':item['sha256']}]))
        self.assertEqual(refused['status'],'failed');self.assertFalse(any(payload is not None for _,payload,_ in self.github.calls))
        summary=await self.owner.request('snapshot',{})
        self.assertTrue(all('review' not in row and 'report' not in row and 'attachment' not in row for row in summary['items']));self.assertNotIn('feedbackDraft',json.dumps(summary))
        with self.assertRaisesRegex(ValueError,'Invalid explicit'):
            await self.action('submit',self.submission('private-draft',draft='Must remain local'))

    async def test_attachment_tamper_and_immutable_diagnostics_fail_before_post(self):
        staged=await self.action('attachment.add',{'requestId':'stage-tamper','resourceUri':'amplifier-feedback-attachment://owned/body','name':'safe.txt','sha256':hashlib.sha256(self.data).hexdigest()})
        item=staged['attachment'];path=Path(self.tmp.name)/'attachments'/item['id']/'content';path.chmod(0o600);path.write_bytes(b'changed')
        result=await self.action('submit',self.submission(attachmentIds=[item['id']]))
        self.assertEqual(result['status'],'failed');self.assertEqual(self.github.calls,[])
        result=await self.action('submit',self.submission('diagnostic-report',deviceDiagnostics={'browser':'Firefox','width':800}))
        saved=await self.action('diagnostics',{'requestId':'diagnostic-report'})
        self.assertEqual(saved['diagnostics'],result['diagnostics'])
        self.assertNotIn(self.tmp.name,json.dumps(saved))

    async def test_reviewed_public_excerpt_writes_exact_bytes_once_and_keeps_origin(self):
        self.github.private=False
        args={'requestId':'public-review','sessionId':'ahp-session:/selected','resourceUri':'amplifier-export:/snapshot'}
        review=(await self.action('excerpt.review',args))['review']
        item=(await self.action('excerpt.stage',{'requestId':'public-stage','sessionId':args['sessionId'],'reviewId':review['reviewId'],'acknowledgeDisclosure':True}))['attachment']
        submit=self.submission(attachmentIds=[item['id']],confirmExcerpts=True,confirmedExcerpts=[{'id':item['id'],'sha256':review['sha256']}])
        first=await self.action('submit',submit)
        self.assertEqual(first['status'],'completed');self.assertEqual(await self.action('submit',submit),first)
        blobs=[payload for endpoint,payload,_ in self.github.calls if endpoint.endswith('/git/blobs')]
        self.assertEqual(len(blobs),1);self.assertEqual(base64.b64decode(blobs[0]['content']),review['text'].encode())
        self.assertEqual((Path(self.tmp.name)/'attachments'/item['id']/'content').read_bytes(),review['text'].encode())
        self.assertEqual(sum(bool(payload) and endpoint.endswith('/issues') for endpoint,payload,_ in self.github.calls),1)

    async def test_receipt_pages_remain_bounded_and_exact_reads_work_after_restart(self):
        for index in range(60):
            identity=f'fixture-{index:05}'
            value={'requestId':identity,'operation':'feedback.get','status':'completed','report':{'body':'not in summaries'}}
            self.owner.db.execute('INSERT INTO commands VALUES(?,?,?,?,?,?)',(identity,'x','feedback.get','{}','completed',json.dumps(value)))
        first=await self.action('list',{'limit':20});second=await self.action('list',{'limit':20,'cursor':first['nextCursor']})
        self.assertEqual(len(first['items']),20);self.assertEqual(len(second['items']),20)
        self.assertTrue({row['requestId'] for row in first['items']}.isdisjoint({row['requestId'] for row in second['items']}))
        self.assertNotIn('not in summaries',json.dumps(first))
        self.assertEqual((await self.action('receipt',{'requestId':'fixture-00000'}))['report']['body'],'not in summaries')


    async def test_remote_call_lifetime_refuses_fence_then_wakes_and_preserves_restart(self):
        entered, finish = asyncio.Event(), asyncio.Event()
        original = self.owner.github
        async def held(*args, **kwargs):
            if args[0].endswith('/issues') and args[1]:
                entered.set()
                await finish.wait()
            return await original(*args, **kwargs)
        self.owner.github = held
        events=[]
        async def notify(method,args): events.append(method)
        self.owner.notify=notify
        context=dict(fenceId='fence',commandId='update',purpose='distribution-update',instanceId='host',dataScope='owned')
        active=asyncio.create_task(self.action('submit',self.submission()))
        await entered.wait()
        self.assertFalse((await self.owner.request('quiescence.acquire',context))['acquired'])
        finish.set(); await active
        self.assertIn('owner/idle',events)
        self.assertTrue((await self.owner.request('quiescence.acquire',context))['acquired'])
        with self.assertRaisesRegex(ValueError,'intake is closed'):
            await self.action('submit',self.submission('new'))
        self.assertEqual((await self.action('receipt',{'requestId':'original-request'}))['status'],'completed')
        await self.owner.close();self.owner=Owner({'dataDir':self.tmp.name},self.host,self.notify,github=self.github)
        self.assertTrue((await self.owner.request('quiescence.inspect',{}))['intakeClosed'])
        proof={**context,'verified':True,'outcome':'unchanged','receiptId':'native-unchanged-proof'}
        await self.owner.request('quiescence.release',{**context,'outcome':'unchanged','proof':proof})
        self.assertEqual((await self.action('get',{'requestId':'read-after','feedbackId':'original-request'}))['status'],'completed')

if __name__=='__main__':unittest.main()
