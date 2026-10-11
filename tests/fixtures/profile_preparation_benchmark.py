"""Owned-DTU, no-inference runtime preparation benchmark; retained state for inspection."""
import argparse, asyncio, json, os, shutil, sys, time, uuid
from pathlib import Path
import yaml

SOURCE=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(SOURCE))
from amplifier_web.runtime_profiles import ensure
from amplifier_web.runtime_environment import prepare_project,receipt_directory
from amplifier_web.updates import process

async def main():
 parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path,required=True);parser.add_argument('--bundle',choices=('local','work'),default='local');args=parser.parse_args();root=args.root.resolve()
 if root.exists():raise RuntimeError('Benchmark root already exists; retain it and inspect before another run')
 root.mkdir(mode=0o700);home=root/'app';shared=root/'shared';workspace=root/'workspace'
 shared.mkdir();workspace.mkdir()
 os.environ['AMPLIFIER_HOME']=str(shared);os.environ['AMPLIFIER_WEB_HOME']=str(home)
 os.environ['AMPLIFIER_UNIFIED_RELEASE']=''
 profile=root/'profile';profile.mkdir();bundle=profile/'bundle.yaml'
 bundle.write_text(yaml.safe_dump({'bundle':{'name':'readiness-profile','version':'1.0.0'},'session':{
  'orchestrator':{'module':'loop-live','source':'git+https://github.com/microsoft/amplifier-module-loop-live@main','config':{'max_iterations':1}},
  'context':{'module':'context-simple','source':'git+https://github.com/microsoft/amplifier-module-context-simple@main'}},
  'providers':[{'module':'provider-openai','source':'git+https://github.com/microsoft/amplifier-module-provider-openai@main','config':{'api_key':'sk-fixture-never-used','default_model':'fixture-no-inference'}}]}))
 selected_bundle='work' if args.bundle=='work' else str(bundle)
 settings={'bundle':{'active':selected_bundle,'app':[]}}
 if args.bundle=='work':
  settings['bundle'].pop('app')
  settings['config']={'providers':[{'module':'provider-openai','config':{'api_key':'sk-fixture-never-used','default_model':'fixture-no-inference'}}]}
 (shared/'settings.yaml').write_text(yaml.safe_dump(settings))
 generation=uuid.uuid4().hex;receipt=receipt_directory(home,generation);(receipt/'foundation').mkdir(parents=True)
 project=prepare_project(home,generation)
 uv=shutil.which('uv');started=time.monotonic()
 print(json.dumps({'phase':'seed-dependency-lock','root':str(root)}),flush=True)
 await process(uv,'lock','--project',str(project),'--python','3.13',timeout=900)
 await process(uv,'sync','--locked','--project',str(project),'--python','3.13',timeout=900)
 (receipt/'profiles-qualified.json').write_text(json.dumps({'profiles':[],'configuration':'synthetic-parent'}))
 results={'baseMain':'73233813c9c15b0b8ea4d1cf89ab6ee509511a3a','seedSeconds':time.monotonic()-started,'calls':[],'limitations':['Linux DTU only','No-inference '+args.bundle+' profile; synthetic provider configuration','Fresh preparation directories; shared download caches may be warm','Does not measure actual model response or worker first-send admission']}
 async def call(name):
  start=time.monotonic()
  async def progress(detail):print(json.dumps({'call':name,'elapsed':time.monotonic()-start,'progress':detail}),flush=True)
  selected=await ensure(home,generation,{'id':name,'workspace':str(workspace),'bundle':selected_bundle},progress=progress)
  row={'name':name,'seconds':time.monotonic()-start,'generation':selected};results['calls'].append(row);print(json.dumps(row),flush=True);return selected
 try:
  one,two=await asyncio.gather(call('cold-one'),call('cold-two'))
  assert one==two and one!=generation
  assert await call('warm-one')==one
  assert await call('warm-two')==one
  if args.bundle=='work':
   from amplifier_web.host.config import WORK_SOURCE
   settings['bundle']['added']={'work':WORK_SOURCE}
   (shared/'settings.yaml').write_text(yaml.safe_dump(settings))
   assert await call('redundant-registration')==one
   assert len(list((home/'updates/releases').glob('*/profile-attempt.json')))==1

  results['attempt']=json.loads((receipt_directory(home,one)/'profile-attempt.json').read_text())
 except BaseException as error:
  facts=getattr(error,'diagnostic_facts',{})
  results['failure']={'errorType':type(error).__name__,**{k:v for k,v in facts.items() if k in ('exitCode','elapsedMs','probe','commandId')}}
  raise
 finally:
  results['attempts']=[json.loads(p.read_text()) for p in (home/'updates/releases').glob('*/profile-attempt.json')]
  (root/'measurements.json').write_text(json.dumps(results,indent=2)+'\n')
 print(json.dumps({'phase':'complete','evidence':str(root/'measurements.json')}),flush=True)

asyncio.run(main())
