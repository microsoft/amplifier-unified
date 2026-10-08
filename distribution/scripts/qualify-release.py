#!/usr/bin/env python3
"""Required Linux installed-release rehearsal. Never qualifies by skipped tests."""
from pathlib import Path
import argparse,hashlib,json,os,re,shutil,subprocess,sys,tarfile,time

def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('configuration',type=Path)
    parser.add_argument('output',type=Path,help='New private evidence directory; never reused')
    args=parser.parse_args()
    if sys.platform!='linux':raise ValueError('Linux is required for the manual systemd and resource-limited gates')
    config=json.loads(args.configuration.read_text())
    required=['archive','archiveSha256','pythonHome','nativeManifest','nativeManifestSha256','legacyPython','legacySource','legacyArchive','legacyArchiveSha256','legacyRevision','questionsSource','schedulesSource','observationsSource','providerSource','webTests','playwright','browsers','node','allowedCpus']
    for key in required:
        if not config.get(key):raise ValueError('Missing release input: '+key)
    if config.get('browserRunner'):
        if not config.get('browserEnvironment') or digest(config['browserRunner'])!=config.get('browserRunnerSha256'):
            raise ValueError('External browser runner requires an exact digest and declared environment')
    for key in ['archiveSha256','nativeManifestSha256','legacyArchiveSha256']:
        if not re.fullmatch(r'[a-f0-9]{64}',config[key]):raise ValueError('Invalid digest: '+key)
    if digest(config['archive'])!=config['archiveSha256']:raise ValueError('Candidate archive changed')
    if digest(config['nativeManifest'])!=config['nativeManifestSha256']:raise ValueError('Native source manifest changed')
    if digest(config['legacyArchive'])!=config['legacyArchiveSha256']:raise ValueError('Legacy archive changed')
    with tarfile.open(config['legacyArchive']) as legacy:
        for member in legacy.getmembers():
            if member.isfile():
                if Path(member.name).is_absolute() or '..' in Path(member.name).parts:raise ValueError('Unsafe legacy archive path')
                if (Path(config['legacySource'])/member.name).read_bytes()!=legacy.extractfile(member).read():raise ValueError('Legacy source changed: '+member.name)
    manifest=json.loads(Path(config['nativeManifest']).read_text())
    native_count=0
    for row in manifest['sources']:
        for relative,expected in row['files'].items():
            if digest(Path(row['sourceRoot'])/relative)!=expected:raise ValueError('Native code changed: '+relative)
            native_count+=1
    for key in ['archive','pythonHome','legacySource','questionsSource','schedulesSource','observationsSource','providerSource','webTests','playwright','browsers','node']:
        config[key]=str(Path(config[key]).resolve(strict=True))
    # Python determines its virtual environment from the invoked path. Resolving
    # a venv's symlink would invoke the base interpreter without its dependencies.
    legacy_python=Path(config['legacyPython']).absolute()
    if not legacy_python.is_file() or not os.access(legacy_python,os.X_OK):
        raise ValueError('Legacy Python must be an existing executable')
    config['legacyPython']=str(legacy_python)
    if not re.fullmatch(r'\d+,\d+',str(config['allowedCpus'])):raise ValueError('Select exactly two available CPUs, e.g. 0,1')
    if not re.fullmatch(r'[0-9a-f]{40}',config['legacyRevision']):raise ValueError('Exact legacy source revision required')
    out=args.output.resolve();out.mkdir(mode=0o700,parents=True,exist_ok=False)
    os.umask(0o077)
    env={k:v for k,v in os.environ.items() if not k.startswith(('AMPLIFIER_','PYTHON','OPENAI_','ANTHROPIC_','AZURE_','UV_','LEGACY_','FULL_OWNER_','OWNER_','COLD_','TEST_')) and k!='VIRTUAL_ENV'}
    env.update(PYTHONDONTWRITEBYTECODE='1',NODE_PATH='',PLAYWRIGHT_BROWSERS_PATH=config['browsers'])
    env.pop('LD_LIBRARY_PATH',None)
    env.pop('WEBKIT_DISABLE_SANDBOX_THIS_IS_DANGEROUS',None)
    node=config['node']
    env['PATH']=str(Path(node).parent)+':'+str(Path(config['pythonHome'])/'bin')+':'+env.get('PATH','/usr/bin:/bin')
    subprocess.run(['systemctl','--user','show-environment'],stdout=subprocess.DEVNULL,env=env,check=True,timeout=10)
    work=out/'consumer';work.mkdir()
    with tarfile.open(config['archive']) as archive:
        for member in archive.getmembers():
            parts=Path(member.name).parts
            if not parts or parts[0]!='package' or '..' in parts:raise ValueError('Unsafe archive path')
        archive.extractall(work,filter='data')
    package=work/'package'
    # Fixture-only test sources are adjacent to, never substituted for, packed runtime files.
    source=Path(__file__).resolve().parents[1]
    test_hashes={str(p.relative_to(source)):digest(p) for p in sorted((source/'test').rglob('*')) if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc'}
    test_digest=hashlib.sha256(json.dumps(test_hashes,sort_keys=True).encode()).hexdigest()
    shutil.copytree(source/'test',package/'test')
    subprocess.run(['cp','-a','--reflink=auto',config['pythonHome'],str(out/'python')],check=True)
    python=str(out/'python/bin/python')
    entry=str(package/'src/index.js')
    env.update(UNIFIED_DISTRIBUTION_ENTRY=entry,UNIFIED_DISTRIBUTION_ARCHIVE=config['archive'],
      AMPLIFIER_ACP_PYTHON=python,UNIFIED_OWNERS_PYTHON=python,UNIFIED_CATALOG_PYTHON=python,
      RECOVERY_NATIVE_PROVIDER=config['providerSource'],COLD_WORKING_SET_PYTHON=python,
      COLD_WORKING_SET_RECEIPT=str(out/'cold.json'),FULL_OWNER_SERVICE='1',FULL_OWNER_SERVICE_MODE='manual-systemd',
      FULL_OWNER_SERVICE_RECEIPT=str(out/'manual-service.json'),FULL_OWNER_ARCHIVE='1',LEGACY_FULL_OWNER_SWITCH='1',
      FULL_OWNER_RESTORE='1',FULL_OWNER_JOURNEY='1',FULL_OWNER_RECOVERY_FAULTS='1',FULL_RECOVERY_PYTHON=python,
      OWNER_SNAPSHOT_PYTHON=python,LEGACY_READBACK_PYTHON=config['legacyPython'],LEGACY_UNIFIED_SOURCE=config['legacySource'],
      LEGACY_HTTP_SOURCE=config['legacySource'],LEGACY_QUESTIONS_SOURCE=config['questionsSource'],
      LEGACY_SCHEDULES_SOURCE=config['schedulesSource'],LEGACY_OBSERVATIONS_SOURCE=config['observationsSource'],
      REHEARSAL_WEB=str(package/'web'),REHEARSAL_PLAYWRIGHT=config['playwright'],
      FULL_OWNER_RELEASE_ARCHIVE=config['archive'],FULL_OWNER_RELEASE_SHA256=config['archiveSha256'],
      DISTRIBUTION_MODULE=entry,HOST_MODULE=str(package/'node_modules/@amplifier/unified-host/dist/index.js'),
      DIAGNOSTICS_MODULE=str(package/'node_modules/@amplifier/unified-diagnostics-capability/src/index.js'),
      NATIVE_CAPABILITY_MODULE=str(package/'node_modules/@amplifier/unified-native-capabilities/dist/index.js'),
      OWNER_PYTHON=python,WEB_DIRECTORY=str(package/'web'),ACCEPTANCE_DIR=str(out/'recording'),
      TEST_PACKED_WEB_DIR=str(package/'web'),RELEASE_PERFORMANCE_OUTPUT=str(out/'performance.json'),
      RELEASE_PERFORMANCE_BASE=str(out/'performance'))
    receipt={'schema':'linux-installed-release-gates-v1','passed':False,'archiveSha256':config['archiveSha256'],
      'nativeManifestSha256':config['nativeManifestSha256'],'nativeFilesVerified':native_count,
      'legacyRevision':config['legacyRevision'],'legacyArchiveSha256':config['legacyArchiveSha256'],
      'runnerSha256':digest(__file__),'testSourcesSha256':test_digest,'testFiles':len(test_hashes),'startedAt':time.time(),'checks':[],
      'webkitBrowserSandboxDisabled':config.get('webkitBrowserSandboxDisabled') is True,
      'browserRunnerSha256':config.get('browserRunnerSha256'),
      'browserEnvironment':config.get('browserEnvironment','local Linux'),
      'limits':['Synthetic provider and private test state; not real-account or physical-audio acceptance.',
        'Existing legacy chat continuation after candidate writes is qualified; all-database reverse migration and new-machine restoration are separate gates.',
        'Passed Linux gates do not authorize production promotion or qualify other operating systems.']}
    def save(): (out/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    def run(name,command,extra=None,timeout=660,tap=False):
        log=out/(name+'.log');runenv={**env,**(extra or {})}
        print('Running '+name,flush=True)
        with log.open('x') as stream:
            # Public release files must retain their declared 0644/0755 modes.
            # Evidence remains beneath the private 0700 parent; fixtures create
            # their own private state roots and explicit private configuration.
            result=subprocess.run(command,cwd=package,env=runenv,stdout=stream,stderr=subprocess.STDOUT,timeout=timeout,umask=0o022)
        text=log.read_text()
        if result.returncode:raise RuntimeError(name+' failed; preserved '+str(log))
        if tap and not (re.search(r'# pass [1-9][0-9]*\b',text) and re.search(r'# fail 0\b',text) and re.search(r'# skipped 0\b',text)):
            raise RuntimeError(name+' did not conclusively pass without skips')
        receipt['checks'].append({'id':name,'passed':True,'logSha256':digest(log)});save()
        return text
    save()
    try:
        run('cold-history',[node,'--test','--test-reporter=tap','test/cold-working-set.integration.test.mjs'],tap=True)
        run('manual-service',[node,'--test','--test-reporter=tap','test/full-owner-service.test.mjs'],tap=True)
        run('streaming',['systemd-run','--user','--scope','--quiet','-p','AllowedCPUs='+config['allowedCpus'],'-p','CPUQuota=200%','-p','MemoryMax=4G','-p','MemorySwapMax=0',node,'--expose-gc','test/release-streaming-performance.mjs'])
        run('recording',[node,str(Path(config['webTests'])/'provider-recording-browser.mjs')])
        for engine in ['chromium','firefox','webkit']:
            browser_env={'TEST_BROWSER':engine,'TEST_OUTPUT':str(out/('attachments-'+engine))}
            if engine=='webkit' and config.get('webkitBrowserSandboxDisabled') is True:
                browser_env['WEBKIT_DISABLE_SANDBOX_THIS_IS_DANGEROUS']='1'
            if config.get('browserRunner'):
                run('attachments-'+engine,[sys.executable,config['browserRunner'],'--archive',config['archive'],
                  '--sha256',config['archiveSha256'],'--engine',engine,'--output',browser_env['TEST_OUTPUT']])
            else:
                run('attachments-'+engine,[node,str(Path(config['webTests'])/'attachment-engine-browser.mjs')],browser_env)
            browser_receipt=json.loads((Path(browser_env['TEST_OUTPUT'])/'receipt.json').read_text())
            if browser_receipt.get('passed') is not True or browser_receipt.get('engine')!=engine or browser_receipt.get('errors')!=[]:
                raise ValueError('Browser lane did not return an exact successful receipt: '+engine)
            if config.get('browserRunner') and browser_receipt.get('archiveSha256')!=config['archiveSha256']:
                raise ValueError('External browser lane did not qualify the exact candidate archive')
        log=run('update-recovery-rollback',[node,'--test','--test-reporter=tap','test/full-owner-archive.integration.test.mjs'],tap=True)
        match=re.search(r'Retained signed full-owner fixture: (.+)',log)
        if not match:raise ValueError('Missing full-owner acceptance receipt')
        archive_receipt=Path(match[1].strip())/'acceptance.json'
        detail=json.loads(archive_receipt.read_text())
        assert len(detail['owners'])==21 and detail['browserJourney']['passed']
        assert detail['activation']['reconciliation']['explicitCompletion']
        assert detail['update']['activated']['status']=='succeeded' and detail['update']['inferenceReplayed'] is False
        assert detail['httpExecution']['result']=='passed' and detail['activation']['startupReplayed'] is False
        shutil.copyfile(archive_receipt,out/'migration-recovery.json')
        # Do not allow the test harness or runtime to quietly substitute candidate code.
        with tarfile.open(config['archive']) as archive:
            for member in archive.getmembers():
                if member.isfile() and (package/Path(member.name).relative_to('package')).read_bytes()!=archive.extractfile(member).read():
                    raise ValueError('Installed runtime file changed during qualification: '+member.name)
        receipt.update(passed=True,completedAt=time.time());save()
        print(json.dumps({'passed':True,'checks':len(receipt['checks']),'receipt':str(out/'receipt.json')}))
    except BaseException as error:
        receipt.update(error=str(error),completedAt=time.time());save();raise
if __name__=='__main__':main()
