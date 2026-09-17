"""Private GitHub release channel; candidate tool installation is isolated."""
import asyncio
import json
import os
from pathlib import Path
import re
import shutil
import sys
import time
from importlib import metadata
from . import __version__
from .host.config import write_private
from .updates import process

REPOSITORY='bkrabach/amplifier-unified'
SOURCE='https://github.com/'+REPOSITORY
PROBE='from amplifier_web.server import create_app; from amplifier_web import __version__; from pathlib import Path; import amplifier_web; p=Path(amplifier_web.__file__).parent; assert (p/"static/index.html").is_file(); print(__version__)'

def version_tuple(value):
    match=re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)',value or '')
    return tuple(map(int,match.groups())) if match else None

def git_environment():
    env={**os.environ,'GIT_TERMINAL_PROMPT':'0'}
    if shutil.which('gh'):
        # Use the existing authenticated GitHub credential helper; never read or
        # transmit tokens through application state or command arguments.
        count=int(env.get('GIT_CONFIG_COUNT','0'))
        env.update({f'GIT_CONFIG_KEY_{count}':'credential.https://github.com.helper',f'GIT_CONFIG_VALUE_{count}':'!gh auth git-credential','GIT_CONFIG_COUNT':str(count+1)})
    return env

async def check():
    base={'id':'application','label':'Amplifier Unified','kind':'app','current':__version__,'repository':SOURCE}
    if not shutil.which('gh'):return {**base,'status':'check_failed','detail':'Sign in with GitHub CLI to check this private release channel.'}
    try:
        data=json.loads(await process('gh','api',f'repos/{REPOSITORY}/releases/latest',timeout=30))
        tag=data['tag_name'];version=version_tuple(tag)
        if not version:raise ValueError('Unsupported release tag')
        output=await process('git','ls-remote',SOURCE,'refs/tags/'+tag,'refs/tags/'+tag+'^{}',env=git_environment(),timeout=30)
        rows=[line.split() for line in output.splitlines()]
        revision=next((row[0] for row in rows if row[1].endswith('^{}')),rows[0][0] if rows else '')
        if not re.fullmatch('[0-9a-f]{40}',revision):raise ValueError('Release revision not found')
        return {**base,'status':'update' if version>version_tuple(__version__) else 'current','latest':tag,'revision':revision,'url':data.get('html_url',SOURCE+'/releases')}
    except (ValueError,KeyError,RuntimeError,TimeoutError):
        return {**base,'status':'check_failed','detail':'No accessible published release was found. Check GitHub sign-in and release availability.'}

async def stage(manager):
    release=manager.service.state['updates'].get('application',{})
    if release.get('status')!='update':raise ValueError('Check for an application release first')
    revision=release['revision']
    if not re.fullmatch('[0-9a-f]{40}',revision):raise ValueError('Invalid release revision')
    folder=manager.directory/'applications'/revision
    folder.mkdir(parents=True,exist_ok=True,mode=0o700)
    env={**git_environment(),'UV_TOOL_DIR':str(folder/'tools'),'UV_TOOL_BIN_DIR':str(folder/'bin')}
    uv=shutil.which('uv')
    if not uv:raise ValueError('Install uv before updating the application')
    await manager.publish(phase='staging',detail='Installing the app release in an isolated environment…',error=None)
    await process(uv,'tool','install','--force','git+'+SOURCE+'@'+revision,env=env,timeout=900)
    python=folder/'tools/amplifier-unified'/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
    installed=await process(python,'-c',PROBE,timeout=30)
    if version_tuple(installed)!=version_tuple(release['latest']):raise ValueError('The release tag does not match its package version')
    write_private(folder/'validated.json',json.dumps({'revision':revision,'version':installed,'hostVersion':__version__}))
    await manager.publish(phase='app-staged',pendingApp=release,detail='Application release validated. Waiting for work to finish before restarting.')

async def activate(manager):
    if manager.lock.locked() or manager.closed:return
    async with manager.lock:
        await _activate(manager)

async def installed_target():
    """Only replace the uv tool environment that owns this running host."""
    uv=shutil.which('uv')
    executable=shutil.which('amplifier-unified')
    if not uv or not executable:
        raise ValueError('Install and launch this app with uv tool before applying application updates')
    env=git_environment()
    tool_root=Path(await process(uv,'tool','dir',env=env,timeout=10)).expanduser().resolve()
    bin_root=Path(await process(uv,'tool','dir','--bin',env=env,timeout=10)).expanduser().resolve()
    prefix=tool_root/'amplifier-unified'
    launcher=prefix/('Scripts/amplifier-unified.exe' if os.name=='nt' else 'bin/amplifier-unified')
    public_launcher=bin_root/('amplifier-unified.exe' if os.name=='nt' else 'amplifier-unified')
    if Path(sys.prefix).resolve()!=prefix.resolve() or Path(executable).resolve()!=launcher.resolve() or public_launcher.resolve()!=launcher.resolve():
        raise ValueError('This host is not running from the active uv tool installation. Restart it using that installation before updating; no global tool was changed.')
    python=prefix/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
    previous={'version':__version__,'installation':str(prefix),'source':None}
    try:
        info=json.loads(metadata.distribution('amplifier-unified').read_text('direct_url.json') or '{}')
        source=info.get('url')
        if source and info.get('vcs_info',{}).get('commit_id'):
            source='git+'+source+'@'+info['vcs_info']['commit_id']
            if info.get('subdirectory'):source+='#subdirectory='+info['subdirectory']
        previous['source']=source
    except (metadata.PackageNotFoundError,ValueError):
        pass
    return uv,str(public_launcher),python,previous

async def _activate(manager):
    release=manager.service.state['updates'].get('pendingApp')
    if not release:return
    revision=release.get('revision','')
    if not re.fullmatch('[0-9a-f]{40}',revision):raise ValueError('Invalid staged release revision')
    marker=manager.directory/'applications'/revision/'validated.json'
    if not marker.exists() or json.loads(marker.read_text()).get('revision')!=revision:raise ValueError('App release must pass isolated validation before activation')
    validated=json.loads(marker.read_text())
    if version_tuple(validated.get('version'))!=version_tuple(release.get('latest')):
        raise ValueError('The pending release does not match its validated package')
    async with manager.service.lock:
        if manager.busy():return
    uv,executable,installed_python,previous=await installed_target()
    helper_python=marker.parent/'tools/amplifier-unified'/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
    if not helper_python.is_file():
        raise ValueError('The validated app environment is missing; stage the release again')
    async with manager.service.lock:
        if manager.busy():return
        manager.service.state['updates'].update(phase='activating',detail='Installing the app update and restarting…')
        manager.service._publish()
    if manager.service.runtime:await manager.service.runtime.close()
    write_private(manager.directory/'previous-app.json',json.dumps(previous))
    try:
        await process(uv,'tool','install','--force','git+'+SOURCE+'@'+release['revision'],env=git_environment(),timeout=900)
        installed=await process(installed_python,'-c',PROBE,timeout=30)
        if version_tuple(installed)!=version_tuple(validated['version']):
            raise ValueError('Installed package differs from the validated release')
    except asyncio.CancelledError:
        await manager.publish(phase='interrupted',error='Application installation was interrupted. Check or repair the uv tool installation before restarting.')
        raise
    except Exception:
        await manager.publish(phase='error',pendingApp=None,error='App installation or verification failed; the running host was retained. Check the recorded previous installation and repair the uv tool before restarting.')
        return
    # Keep the work gate closed until this process exits. Publishing installed
    # here would permit a new conversation between the helper spawn and SIGTERM.
    await manager.publish(phase='activating',pendingApp=None,appAvailable=False,
        pendingRestart={'version':validated['version'],'revision':revision},detail='Application installed. Restarting the local host…')
    # A tiny stdlib helper waits until this host releases its port, then starts
    # the already-verified launcher. It does not execute a shell command.
    options=[executable,'--no-open','--port',str(manager.service.port),'--data-dir',str(manager.home),'--workspace',manager.service.default_workspace]
    helper=manager.directory/'restart.py'
    write_private(helper,'''import json,os,subprocess,sys,time
parent=int(sys.argv[1]);args=json.loads(sys.argv[2]);logpath=sys.argv[3]
for _ in range(120):
 try: os.kill(parent,0)
 except OSError: break
 time.sleep(.5)
else: raise SystemExit('Old host did not exit')
with open(logpath,'a') as log:
 os.chmod(logpath,0o600)
 subprocess.Popen(args,start_new_session=True,stdout=log,stderr=log)
''')
    try:
        await asyncio.create_subprocess_exec(str(helper_python),str(helper),str(os.getpid()),json.dumps(options),str(manager.directory/'restart.log'),start_new_session=True,stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL)
    except (OSError,ValueError):
        await manager.publish(phase='error',pendingRestart=None,error='The update installed, but its restart helper could not start. Restart Amplifier Unified from the terminal.')
        return
    import signal
    os.kill(os.getpid(),signal.SIGTERM)
