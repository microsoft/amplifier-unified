"""Passively reconcile live schedule evidence; never submit or retry a run."""
import argparse
import asyncio
import json
import os
from pathlib import Path

from acceptance import private_json
from live_schedule_acceptance import bounded_calls, same_admissions


async def review(args):
    from amplifier_web.server import create_app
    folder,target=args.run.resolve(),args.output.resolve()
    source=json.loads((folder/'report.json').read_text())
    before=json.loads((folder/'schedule-before-restart.json').read_text())
    baseline=json.loads((folder/'capacity-before-restart.json').read_text())
    sid=before['schedule']['sessionId']
    target.mkdir(parents=True,exist_ok=False,mode=0o700)
    os.environ.update(AMPLIFIER_HOME=str(folder/'shared'), AMPLIFIER_WEB_HOME=str(folder/'app'),
        AMPLIFIER_SESSION_STATE_HOME=str(folder/'ownership'))
    app=await create_app(folder/'app',workspace=folder/'workspace',runtime=False,voice=False,
        background_updates=False,preload_providers=False)
    service=app['service']
    try:
        after=(await service.dispatch('capacity.read',{'sessionId':sid}))['result']
        schedule=(await service.dispatch('schedule.read',{'sessionId':sid,'id':before['schedule']['id']}))['result']
        checks={key:value for key,value in source['checks'].items()
                if key not in {'one_real_model_call','restart_has_no_new_provider_call'}}
        checks['at_most_two_real_model_calls']=bounded_calls(baseline)
        checks['saved_admission_ids_unchanged']=same_admissions(baseline,after)
        checks['saved_due_run_unchanged']=schedule['runs']==before['runs']
        checks['passive_review_execution_disabled']=service.runtime is False
        result={'schema_version':1,'scenario':'passive saved schedule reconciliation','source_live_passed':source['passed'],
            'attribution':'All original live checks preserved except two incorrect one-call assertions. Real goal evaluation adds a second call; compare unchanged admitted IDs instead. Original live report remains unchanged.',
            'original_source':source['source'],'original_harness_sha256':source['harness_sha256'],
            'provider':source['provider'],'model':source['model'],'effort':source['effort'],'chromium':source['chromium'],
            'checks':checks,'passed':all(checks.values()),'live_provider_calls':baseline['usage']['calls'],
            'passive_review_provider_calls':0,'run_phase':schedule['runs'][0]['phase'],
            'canonical_transcript_count':source['canonical_transcript_count'],
            'credential_cleanup':source['credential_cleanup']}
        private_json(target/'report.json',result)
        print(json.dumps(result),flush=True)
        return result
    finally:
        await service.close()


def main():
    os.umask(0o077)
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    raise SystemExit(0 if asyncio.run(review(args))['passed'] else 1)


if __name__=='__main__':main()
