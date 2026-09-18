"""Opt-in seam proof against an actual isolated Context Intelligence server.

UNIFIED_CI_TEST_URL=http://127.0.0.1:18081
UNIFIED_CI_TEST_KEY_FILE=/private/path/to/test-only-key
No model calls or real user data. This writes synthetic, uniquely named records.
"""
import asyncio
import copy
import json
import os
from pathlib import Path
import uuid
import pytest
from context_intelligence import AsyncCIClient
from amplifier_web.diagnostics import DEFAULT
from amplifier_web.service import AppService

pytestmark=pytest.mark.skipif(not os.environ.get('UNIFIED_CI_TEST_URL') or not os.environ.get('UNIFIED_CI_TEST_KEY_FILE'),reason='isolated CI server not configured')

async def test_real_app_stream_routes_auth_and_graph(tmp_path,monkeypatch):
    key=Path(os.environ['UNIFIED_CI_TEST_KEY_FILE']).read_text().strip()
    url=os.environ['UNIFIED_CI_TEST_URL'];monkeypatch.setenv('UNIFIED_SYNTHETIC_CI_KEY',key)
    service=AppService(tmp_path,workspace=tmp_path);root='unified-proof-'+str(uuid.uuid4())
    try:
        destinations=[{'id':identity,'name':identity,'url':url,'enabled':True,'streams':[stream],'workspace':'proof-'+identity,'apiKeyEnv':'UNIFIED_SYNTHETIC_CI_KEY'} for identity,stream in [('personal','updates'),('team','usage')]]
        await service.dispatch('diagnostics.configure',{'config':{**copy.deepcopy(DEFAULT),'destinations':destinations}},origin='agent')
        collector=service.diagnostics
        collector.record('updates',{'event':'update:proof','data':{'phase':'probe','attemptId':'synthetic-attempt'}},session_id=root)
        collector.record('usage',{'event':'provider:request','data':{'model':'synthetic','provider':'fixture'}},session_id=root+'-child',parent_id=root)
        collector.record('usage',{'event':'llm:response','data':{'model':'synthetic','provider':'fixture','usage':{'input_tokens':10,'output_tokens':5}}},session_id=root+'-child',parent_id=root)
        collector.record('conversation',{'event':'content:user','data':{'text':'CONTENT_MUST_NOT_BE_SENT'}},session_id=root)
        await collector.flush()
        for destination in collector.config['destinations']:await collector.deliver(destination)
        with collector._db() as db:
            receipts=[tuple(row) for row in db.execute('SELECT destination,status,count(*) FROM deliveries GROUP BY destination,status')]
        assert receipts==[('personal','accepted',1),('team','accepted',2)]
        client=AsyncCIClient(url,api_key=key)
        for _ in range(50):
            rows=await client.cypher('MATCH (n) WHERE n.session_id IN $ids RETURN labels(n) AS labels, properties(n) AS properties',params={'ids':[root,root+'-child']})
            text=json.dumps(rows)
            if 'proof-personal' in text and 'proof-team' in text:break
            await asyncio.sleep(.2)
        assert 'proof-personal' in text and 'proof-team' in text
        assert 'CONTENT_MUST_NOT_BE_SENT' not in text
        assert (await collector.test('personal'))['phase']=='ready'
        monkeypatch.setenv('UNIFIED_SYNTHETIC_CI_KEY','invalid-synthetic-test-key')
        assert (await collector.test('personal'))['error']['statusCode']==401
        record=await service.dispatch('diagnostics.records',{'stream':'updates'},origin='agent')
        assert len(record['result']['items'])==1
    finally:await service.close()
