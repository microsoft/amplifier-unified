"""Opt-in bounded natural Work tasks; expected routing is never sent to the model."""
from __future__ import annotations
import argparse
import asyncio
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import time
import yaml

from acceptance import setup, private_json, installed_revisions, Observation


def calls(messages):
    result=[]
    for message in messages:
        for call in message.get('tool_calls') or []:
            function=call.get('function',call)
            args=function.get('arguments') or function.get('input',{})
            if isinstance(args,str):
                try: args=json.loads(args)
                except ValueError: args={}
            result.append({'name':function.get('name') or function.get('tool'),'arguments':args})
    return result


def completed(events, sid):
    return any(event.get('kind') == 'runtime.generation'
        and event.get('sessionId') == sid and event.get('event') == 'generation.finished'
        for event in events)


def final_text(messages):
    for message in reversed(messages):
        if message.get('role') != 'assistant': continue
        content = message.get('content', '')
        text = content if isinstance(content, str) else '\n'.join(
            block.get('text', '') for block in content if block.get('type') == 'text')
        if text.strip(): return text
    return ''


def near_miss_checks(case_id, text, output_hashes):
    import re
    if case_id == 'csv-definition':
        return {'correct_definition': bool(re.search(r'comma[ -]separated values', text, re.I)),
            'no_artifact': not output_hashes}
    if case_id == 'slide-wording':
        return {'four_word_title': len(re.findall(r"[\w]+(?:['’-][\w]+)*", text)) == 4,
            'no_artifact': not output_hashes}
    return None


def hash_outputs(workspace):
    return {str(p.relative_to(workspace)):hashlib.sha256(p.read_bytes()).hexdigest()
        for p in (workspace/'outputs').rglob('*') if p.is_file()} if (workspace/'outputs').exists() else {}


async def run(args):
    from aiohttp import web
    from amplifier_web.server import create_app
    from amplifier_web.session_client import SessionClient
    cases=json.loads((args.work_source/'evals/natural-cases.json').read_text())['cases']
    cases=[case for case in cases if case['id'] in args.case]
    if len(cases)!=len(set(args.case)) or len(cases)>3: raise ValueError('Select one to three known cases')
    folder,provider=setup(args)
    os.environ['AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH']=str(folder/'shared/projects')
    (folder/'isolated-user-skills').mkdir()
    settings=folder/'shared/settings.yaml'; data=yaml.safe_load(settings.read_text())
    data['bundle']={'active':str(args.work_source/'bundle.md'),'added':{'work':str(args.work_source/'bundle.md')}}
    data['config']['session']={'orchestrator':{'config':{'max_iterations':20,'background_delegate':False}}}
    data['config']['tools']=[{'module':'tool-skills','config':{'skills':['.amplifier/skills','.agents/skills',str(folder/'isolated-user-skills'),'@work-skills:skills','@unified:skills']}}]
    settings.write_text(yaml.safe_dump(data))
    spec=importlib.util.spec_from_file_location('fidelity',args.work_source/'evals/fidelity.py');fidelity=importlib.util.module_from_spec(spec);spec.loader.exec_module(fidelity)
    report={'provider':{'id':args.provider,'module':provider['module'],'model':provider['config'].get('default_model'),'effort':provider['config'].get('reasoning_effort')},'installed':installed_revisions(),'cases':[],
        'limits':{'cases':len(cases),'iterations_per_turn':20,'seconds_per_turn':args.timeout},'baseline':'none; no comparative quality claim'}
    for name,root in [('work',args.work_source),('unified',Path(__file__).resolve().parents[2])]:
        report[name+'_revision']=subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip()
        report[name+'_diff_sha256']=hashlib.sha256(subprocess.check_output(['git','-C',str(root),'diff','HEAD'])).hexdigest()
    app=await create_app(folder/'app',workspace=folder/'workspace',voice=False,background_updates=False,preload_providers=False)
    service=app['service'];observed=Observation();original=service.on_runtime_event
    async def event(kind,data):observed.add(kind,data);await original(kind,data)
    service.on_runtime_event=event
    runner=web.AppRunner(app);await runner.setup();site=web.TCPSite(runner,'127.0.0.1',0);await site.start()
    url=f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}'
    print(json.dumps({'phase':'ready','url':url}),flush=True)
    try:
        async with SessionClient(url,app['control_token'],'natural-artifact-acceptance') as client:
            for case in cases:
                case_dir=folder/'cases'/case['id'];workspace=case_dir/'workspace';workspace.mkdir(parents=True)
                if case.get('fixture'):fidelity.prepare(workspace)
                for fixture in case.get('fixtures',[]):
                    target=workspace/fixture['path'];target.parent.mkdir(parents=True,exist_ok=True);target.write_text(fixture['content'])
                result={'id':case['id'],'turns':[],'started':time.time(),'outcome':'failed'};sid=None
                try:
                    created=await client.create_session({'title':'Natural acceptance: '+case['id'],'bundle':str(args.work_source/'bundle.md'),'workspace':str(workspace)},command_id='create-'+case['id'])
                    sid=created['state']['selectedSessionId'];result['sessionId']=sid
                    await asyncio.wait_for(service.runtime.start(service._session(sid),service.on_runtime_event),300)
                    result['catalog']=await service.runtime.control(sid,'catalog.inspect')
                    result['runtime_dependencies']=await service.runtime.dependencies(sid,verify=True)
                    for turn,prompt in enumerate(case['turns']):
                        before=hash_outputs(workspace);start=len(observed.events)
                        usage_before=await service.runtime.control(sid,'usage.inspect')
                        await client.command(sid,'conversation.send',{'text':prompt},command_id=f'{case["id"]}-{turn}')
                        async with asyncio.timeout(args.timeout):
                            while True:
                                fresh=observed.events[start:]
                                if service._session(sid)['status'] in {'error','failed'}:raise RuntimeError('Runtime failed')
                                if service._session(sid)['status']=='idle' and completed(fresh, sid):break
                                await asyncio.sleep(.5)
                        history=await service.runtime.control(sid,'history.snapshot');private_json(case_dir/f'history-{turn}.json',history)
                        usage=await service.runtime.control(sid,'usage.inspect');tool_calls=calls(history['messages'])
                        entry={'prompt':prompt,'usage_before':usage_before,'usage_after':usage,'output_hashes':hash_outputs(workspace),'previous_outputs_unchanged':all((workspace/p).exists() and hashlib.sha256((workspace/p).read_bytes()).hexdigest()==sha for p,sha in before.items()),'loaded_skills':sorted({c['arguments']['skill_name'] for c in tool_calls if c['name']=='load_skill' and c['arguments'].get('skill_name')}),'calls':tool_calls}
                        entry['final_text']=final_text(history['messages'])
                        entry['content_checks']=fidelity.verify_memo(workspace,revised=turn==1) if case['id']=='memo-revise' else near_miss_checks(case['id'],entry['final_text'],entry['output_hashes'])
                        result['turns'].append(entry);private_json(case_dir/'result.json',result)
                        print(json.dumps({'phase':'turn-finished','case':case['id'],'turn':turn,'skills':entry['loaded_skills']}),flush=True)
                    loaded=set(result['turns'][-1]['loaded_skills']);result['routing_passed']=set(case['expected_skills'])<=loaded and not set(case.get('forbidden_skills',[]))&loaded
                    result['outcome']='needs-review' if any(t['content_checks'] is None for t in result['turns']) else 'completed' if result['routing_passed'] and all(all(t['content_checks'].values()) and t['previous_outputs_unchanged'] for t in result['turns']) else 'failed'
                except Exception as exc:
                    result['error_type']=type(exc).__name__; (case_dir/'private-error.txt').write_text(str(exc))
                    if sid:
                        try:
                            private_json(case_dir/'failed-history.json',await service.runtime.control(sid,'history.snapshot'))
                            result['usage_at_failure']=await service.runtime.control(sid,'usage.inspect')
                        except Exception:pass
                finally:
                    if sid:await service.runtime.stop(sid)
                    result['elapsed_seconds']=time.time()-result['started'];private_json(case_dir/'result.json',result);report['cases'].append(result);private_json(folder/'report.json',report)
                    print(json.dumps({'phase':'case-finished','case':case['id'],'outcome':result['outcome'],'error_type':result.get('error_type')}),flush=True)
    finally:
        await runner.cleanup();private_json(folder/'events.json',observed.events)
        # Remove only the isolated credential-bearing settings, never originals.
        settings.write_text('config:\n  providers: []\n')
    return all(c['outcome']=='completed' for c in report['cases'])


if __name__=='__main__':
    os.umask(0o077)
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--allow-live',action='store_true');parser.add_argument('--provider',required=True)
    parser.add_argument('--settings',type=Path,default=Path.home()/'.amplifier/settings.yaml')
    parser.add_argument('--work-source',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--case',action='append',required=True);parser.add_argument('--timeout',type=int,default=360)
    args=parser.parse_args()
    if not args.allow_live:parser.error('--allow-live is required')
    raise SystemExit(0 if asyncio.run(run(args)) else 1)
