"""Private GitHub release channel; candidate tool installation is isolated."""
import asyncio
import json
import os
from pathlib import Path
import re
import shutil
import sys
import time
import uuid
from importlib import metadata
from . import __version__
from .host.config import write_private
from .updates import process
from . import app_component_graph as components

REPOSITORY='microsoft/amplifier-unified'
SOURCE='https://github.com/'+REPOSITORY
OPTIONAL_EXTRAS={'native-desktop':'amplifier-module-tool-computer-use','tui':'amplifier-app-tui'}
PROBE = r'''import json,sys
facts={"ok":False,"stage":"imports","isolated":bool(sys.flags.isolated),"pythonVersion":"%s.%s.%s"%sys.version_info[:3]}
try:
 from pathlib import Path
 extras=sys.argv[1:]
 assert len(extras)==len(set(extras)) and all(extra in {"native-desktop","tui"} for extra in extras)
 import amplifier_web
 from amplifier_web import __version__
 from amplifier_web.server import create_app
 import pam
 facts["version"]=__version__
 facts["stage"]="package"
 p=Path(amplifier_web.__file__).resolve().parent
 facts["packageInEnvironment"]=p.is_relative_to(Path(sys.prefix).resolve())
 assert facts["packageInEnvironment"]
 facts["stage"]="assets"
 facts["frontendPresent"]=(p/"static/index.html").is_file()
 assert facts["frontendPresent"]
 facts["stage"]="login"
 facts["loginAvailable"]=callable(pam.authenticate)
 assert facts["loginAvailable"]
 if "native-desktop" in extras:
  facts["stage"]="capabilities"
  from amplifier_module_tool_computer_use import foreground
  assert Path(foreground.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
  assert callable(foreground.local_observer) and issubclass(foreground.ForegroundUnavailable,RuntimeError)
 if "tui" in extras:
  import os
  from amplifier_tui.connected import main
  from amplifier_tui.launcher import executable
  facts["stage"]="terminal"
  binary=executable(None)
  assert callable(main) and binary.is_relative_to(Path(sys.prefix).resolve())
  assert binary.is_file() and os.access(binary,os.X_OK)
 facts.update(ok=True,stage="complete")
except Exception as error:
 name=type(error).__name__
 facts["errorType"]=name if name in {"AssertionError","ImportError","ModuleNotFoundError","FileNotFoundError","PermissionError","OSError","RuntimeError","ValueError"} else "Exception"
print("AMPLIFIER_UPDATE_PROBE="+json.dumps(facts),flush=True)
if not facts["ok"]:raise SystemExit(1)
'''


def verified_version(manager,output,expected,phase):
    from .update_diagnostics import probe_record
    report=probe_record(output)
    version=report.get('version') if report and report.get('ok') else output.strip() if report is None else None
    if version_tuple(version)!=version_tuple(expected) or version_tuple(version) is None:
        facts={'errorType':'ValueError'}
        if version_tuple(expected):facts['expectedVersion']=expected.removeprefix('v')
        if version_tuple(version):facts['observedVersion']=version.removeprefix('v')
        if report:facts['probe']=report
        manager.diagnostics.record(phase,'failed',**facts)
        raise ValueError('The probed package does not match the validated release version')
    return version.removeprefix('v')

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

def installed_extras():
    """Retain known optional features without loading clients or desktop backends."""
    extras=[]
    for extra,distribution in sorted(OPTIONAL_EXTRAS.items()):
        try:metadata.distribution(distribution)
        except metadata.PackageNotFoundError:continue
        extras.append(extra)
    return extras

def validated_extras(extras):
    if (not isinstance(extras,list) or
        any(not isinstance(extra,str) or extra not in OPTIONAL_EXTRAS for extra in extras) or
        len(extras)!=len(set(extras))):
        raise ValueError('Unsupported optional application features')
    return sorted(extras)

def install_requirement(revision,extras):
    extras=validated_extras(extras)
    source='git+'+SOURCE+'@'+revision
    return 'amplifier-unified['+','.join(extras)+'] @ '+source if extras else source

def application_state():
    from .release_notes import history
    return {'id':'application','label':'Amplifier Unified','kind':'app','current':__version__,'repository':SOURCE,
            'channel':'github-releases','status':'not_checked','detail':'Check for a published application release.',
            'releaseNotes':history()}

async def check():
    base=application_state()
    if not shutil.which('gh'):return {**base,'status':'check_failed','detail':'Sign in with GitHub CLI to check this private release channel.'}
    try:
        data=json.loads(await process('gh','api',f'repos/{REPOSITORY}/releases/latest',timeout=30))
        tag=data['tag_name'];version=version_tuple(tag)
        if not version or data.get('draft') or data.get('prerelease'):raise ValueError('Unsupported release tag')
        output=await process('git','ls-remote',SOURCE,'refs/tags/'+tag,'refs/tags/'+tag+'^{}',env=git_environment(),timeout=30)
        rows=[line.split() for line in output.splitlines()]
        revision=next((row[0] for row in rows if row[1].endswith('^{}')),rows[0][0] if rows else '')
        if not re.fullmatch('[0-9a-f]{40}',revision):raise ValueError('Release revision not found')
        current=version_tuple(__version__)
        ahead=version<current
        if version>current:
            # Read only this published, resolved commit; notes on main may describe
            # changes that are not yet installable. Older releases lack this file.
            from .release_notes import parse,history
            try:
                raw=await process('gh','api','-H','Accept: application/vnd.github.raw+json',
                                  f'repos/{REPOSITORY}/contents/amplifier_web/release-notes.json?ref={revision}',timeout=15)
                base['releaseNotes']=history(parse(raw,tag.removeprefix('v')),tag)
            except (ValueError,RuntimeError,TimeoutError):
                base['releaseNotesWarning']='Release notes for the available update could not be loaded. Installed release history is still available.'
        component_updates = await components.updates(process, git_environment())
        # Never downgrade a development host to an older published application.
        refresh_components = bool(component_updates) and not ahead
        return {**base,'status':'update' if version>current or refresh_components else 'current','latest':tag,'revision':revision,
            'componentUpdates':component_updates,
            'url':SOURCE+'/releases/tag/'+tag,'publishedAt':data.get('published_at'),'releaseBehind':ahead,
            'detail':('This installation is newer than the latest published release. Updates follow published releases, not the main branch.' if ahead
                      else 'A newer application release is available; installation restarts the host when idle.' if version>current
                      else 'Newer application components are available; installation restarts the host when idle.' if refresh_components
                      else 'The latest published application release and checked components are installed.')}
    except (ValueError,KeyError,RuntimeError,TimeoutError):
        return {**base,'status':'check_failed','detail':'No accessible published release was found. Check GitHub sign-in and release availability.'}

async def stage(manager):
    manager.diagnostics.begin('application',manager.service.state['updates'].get('application',{}).get('revision'))
    manager.diagnostics.record('stage','started')
    try:await _stage(manager)
    except asyncio.CancelledError:raise
    except Exception:
        phase=manager.diagnostics.state.get('latest',{}).get('phase','stage')
        if manager.diagnostics.state.get('latest',{}).get('status')!='failed':manager.diagnostics.record(phase,'failed',errorType='ValueError')
        await manager.publish(phase='error',error='Application update failed during '+phase.replace('-',' ')+'. The running host was retained.')
        raise


async def _stage(manager):
    release=manager.service.state['updates'].get('application',{})
    if release.get('status')!='update':raise ValueError('Check for an application release first')
    revision=release['revision']
    if not re.fullmatch('[0-9a-f]{40}',revision):raise ValueError('Invalid release revision')
    generation=uuid.uuid4().hex
    folder=manager.directory/'applications'/revision/generation
    folder.mkdir(parents=True,exist_ok=True,mode=0o700)
    env={**git_environment(),'UV_TOOL_DIR':str(folder/'tools'),'UV_TOOL_BIN_DIR':str(folder/'bin')}
    uv=shutil.which('uv')
    if not uv:raise ValueError('Install uv before updating the application')
    extras=validated_extras(installed_extras())
    host_graph=manager.diagnostics.sync('host-components',components.installed_graph)
    await manager.publish(phase='staging',detail='Installing the app release in an isolated environment…',error=None)
    await manager.diagnostics.run('candidate-install',process,uv,'tool','install','--force','--upgrade','--refresh',install_requirement(revision,extras),env=env,timeout=900)
    python=folder/'tools/amplifier-unified'/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
    output=await manager.diagnostics.run('candidate-probe',process,python,'-I','-c',PROBE,*extras,timeout=30)
    installed=verified_version(manager,output,release['latest'],'candidate-version')
    graph=await manager.diagnostics.run('candidate-components',components.read_graph,process,python)
    app=next((item for item in graph if item['name']=='amplifier-unified'),{})
    if app.get('revision')!=revision or app.get('url')!=SOURCE or app.get('version')!=installed:
        raise ValueError('Candidate component graph does not match the selected application')
    manager.diagnostics.sync('candidate-components-record',write_private,folder/'components.txt',components.requirements(graph))
    manager.diagnostics.sync('candidate-record',write_private,folder/'validated.json',json.dumps({'revision':revision,'version':installed,'hostVersion':__version__,'attemptId':manager.diagnostics.state['attemptId'],'extras':extras,'generation':generation,'componentGraph':graph,'componentDigest':components.digest(graph),'hostGraph':host_graph,'hostDigest':components.digest(host_graph)}))
    manager.diagnostics.clear_failure()
    manager.diagnostics.record('stage','succeeded',observedVersion=installed)
    await manager.publish(phase='app-staged',pendingApp={**release,'attemptId':manager.diagnostics.state['attemptId'],'generation':generation},detail='Application release validated. Waiting for work to finish before restarting.')

async def activate(manager):
    if manager.lock.locked() or manager.closed:return
    async with manager.lock:
        try:await _activate(manager)
        except Exception as error:
            from .update_diagnostics import exception_type
            last=manager.diagnostics.state.get('latest',{})
            if last.get('status')!='failed':
                manager.diagnostics.begin('application',manager.service.state['updates'].get('pendingApp',{}).get('revision'))
                manager.diagnostics.record('activation-validation','failed',errorType=exception_type(error))
            state=manager.service.state['updates']
            if isinstance(state.get('pendingApp'),dict) and state['pendingApp'].get('generation') and state.get('phase')!='activating':
                # Preserve the failed generation as evidence, but allow a later
                # explicit Install to create a new candidate. Never retry effects.
                await manager.publish(phase='error',pendingApp=None,appAvailable=True,
                    error='The staged application changed or could not be validated. Install again to prepare a new candidate.',
                    detail='The running host and previous candidate evidence were preserved.')
            raise

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
    generation=release.get('generation')
    if generation is not None and (not isinstance(generation,str) or not re.fullmatch('[0-9a-f]{32}',generation)):
        raise ValueError('Invalid staged application generation')
    folder=manager.directory/'applications'/revision
    if generation:folder=folder/generation
    marker=folder/'validated.json'
    # Validate one snapshot before closing the runtime or touching the install.
    try:
        validated=json.loads(marker.read_text())
    except (FileNotFoundError,NotADirectoryError,json.JSONDecodeError):
        raise ValueError('App release must pass isolated validation before activation') from None
    if not isinstance(validated,dict) or validated.get('revision')!=revision:
        raise ValueError('App release must pass isolated validation before activation')
    if version_tuple(validated.get('version'))!=version_tuple(release.get('latest')):
        raise ValueError('The pending release does not match its validated package')
    try:
        extras=validated_extras(validated.get('extras',[]))
        extras_match=extras==installed_extras()
    except ValueError:
        extras_match=False
    if not extras_match:
        message='Optional features changed after validation. Install the update again to validate the current selection.'
        manager.diagnostics.begin('application',revision,validated.get('attemptId') or release.get('attemptId'))
        manager.diagnostics.record('activation-validation','failed',errorType='ValueError')
        # Removing the pending pointer lets the normal install command stage a
        # fresh candidate. Keep its receipt on disk for diagnosis, never reuse it.
        await manager.publish(phase='error',pendingApp=None,appAvailable=True,error=message,detail=message)
        raise ValueError(message)
    graph=None
    if generation:
        graph=components.validate_graph(validated.get('componentGraph'))
        if validated.get('generation')!=generation or components.digest(graph)!=validated.get('componentDigest'):
            raise ValueError('The component receipt does not match this application generation')
        host_graph=components.validate_graph(validated.get('hostGraph'))
        if components.digest(host_graph)!=validated.get('hostDigest') or components.installed_graph()!=host_graph:
            raise ValueError('The installed host components changed after validation; prepare a new candidate')
        if (folder/'components.txt').read_text()!=components.requirements(graph):
            raise ValueError('The component resolution changed after validation')
    async with manager.service.lock:
        if manager.busy():return
    manager.diagnostics.begin('application',revision,validated.get('attemptId') or release.get('attemptId'))
    uv,executable,installed_python,previous=await manager.diagnostics.run('target-discovery',installed_target)
    helper_python=marker.parent/'tools/amplifier-unified'/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
    if not helper_python.is_file():
        raise ValueError('The validated app environment is missing; stage the release again')
    if graph is not None:
        candidate_graph=await manager.diagnostics.run('candidate-components-recheck',components.read_graph,process,helper_python)
        if candidate_graph!=graph:
            raise ValueError('The candidate components changed after validation; stage a new update')
    async with manager.service.lock:
        if manager.busy():return
        manager.service.state['updates'].update(phase='activating',detail='Installing the app update and restarting…')
        manager.service._publish()
    try:
        if manager.service.runtime:await manager.diagnostics.run('runtime-close',manager.service.runtime.close)
        manager.diagnostics.sync('recovery-record',write_private,manager.directory/'previous-app.json',json.dumps(previous))
        resolution_args=['--overrides',str(folder/'components.txt')] if graph is not None else []
        await manager.diagnostics.run('replacement-install',process,uv,'tool','install','--force',*resolution_args,install_requirement(release['revision'],extras),env=git_environment(),timeout=900)
        output=await manager.diagnostics.run('replacement-probe',process,installed_python,'-I','-c',PROBE,*extras,timeout=30)
        installed=verified_version(manager,output,validated['version'],'replacement-version')
        if graph is not None:
            replacement_graph=await manager.diagnostics.run('replacement-components',components.read_graph,process,installed_python)
            if replacement_graph!=graph:
                raise ValueError('Installed components do not match the qualified application generation')
    except asyncio.CancelledError:
        await manager.publish(phase='interrupted',pendingApp=None,error='Application installation was interrupted. Check or repair the uv tool installation before restarting.')
        raise
    except Exception as error:
        last=manager.diagnostics.state.get('latest',{})
        phase=last.get('phase','activation')
        if last.get('status')!='failed':
            from .update_diagnostics import exception_type
            manager.diagnostics.record(phase,'failed',errorType=exception_type(error))
        await manager.publish(phase='error',pendingApp=None,error='Application update failed during '+phase.replace('-',' ')+'. The running host was retained; review the diagnostic receipt before retrying.')
        return
    # Keep the work gate closed until this process exits. Publishing installed
    # here would permit a new conversation between the helper spawn and SIGTERM.
    manager.diagnostics.clear_failure()
    await manager.publish(phase='activating',pendingApp=None,appAvailable=False,error=None,
        pendingRestart={'version':validated['version'],'revision':revision,'attemptId':manager.diagnostics.state['attemptId'],
                        'sourceInstanceId':manager.running_identity['instanceId'],'requestedAt':time.time()},detail='Application installed. Restarting the local host…')
    # A generated systemd unit owns its process lifecycle.  Asking systemd to
    # restart that unit avoids racing its restart policy with a second detached
    # process spawned by this in-process updater.
    from .deployment_service import current_process_is_unit_managed
    if current_process_is_unit_managed(manager.home):
        await request_managed_restart(manager)
        return
    # A tiny stdlib helper waits until this host releases its port, then starts
    # the already-verified launcher. It does not execute a shell command.
    options=manager.diagnostics.sync('restart-configuration',restart_arguments,manager,executable)
    helper=manager.directory/'restart.py'
    manager.diagnostics.sync('restart-helper-file',write_private,helper,'''import json,os,subprocess,sys,time
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
    from .host.config import worker_environment
    try:
        await manager.diagnostics.run('restart-helper',asyncio.create_subprocess_exec,str(helper_python),str(helper),str(os.getpid()),json.dumps(options),str(manager.directory/'restart.log'),start_new_session=True,stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL,env=worker_environment())
    except (OSError,ValueError):
        await manager.publish(phase='activating',error='The update installed, but its restart helper could not start. Restart Amplifier Unified from the terminal.',
                              detail='Waiting for a healthy restarted host. New work remains paused.')
        return
    import signal
    manager.diagnostics.record('restart-request','succeeded')
    os.kill(os.getpid(),signal.SIGTERM)


async def request_managed_restart(manager):
    """Queue an OS-owned restart; only a ready successor confirms completion.

    Even with --no-block, systemd can stop this entire cgroup before the
    systemctl client receives its reply. A signal, timeout, or cancelled await
    therefore leaves the request outcome unknown, not successful or rejected.
    The durable marker and work gate survive every outcome of the handoff.
    """
    from .deployment_service import UNIT_NAME
    from .update_diagnostics import CommandFailure, exception_type

    marker=dict(manager.service.state['updates']['pendingRestart'])
    marker.update(requestStatus='requesting')
    await manager.publish(phase='activating',pendingRestart=marker)
    command_id=uuid.uuid4().hex
    manager.diagnostics.record('service-restart-request','started',commandId=command_id)
    started=time.monotonic()
    try:
        result=await process('systemctl','--user','--no-block','restart',UNIT_NAME,timeout=30)
    except BaseException as error:
        if not isinstance(error,(Exception,asyncio.CancelledError)):raise
        facts={'durationMs':round((time.monotonic()-started)*1000),'errorType':exception_type(error)}
        facts.update(getattr(error,'diagnostic_facts',{}))
        exit_code=facts.get('exitCode')
        rejected=(isinstance(error,CommandFailure) and type(exit_code) is int and exit_code>0) or (isinstance(error,OSError) and not isinstance(error,TimeoutError))
        status='rejected' if rejected else 'uncertain'
        marker.update(requestStatus=status)
        manager.diagnostics.record('service-restart-request','failed' if rejected else status,commandId=command_id,**facts)
        await manager.publish(phase='activating',pendingRestart=marker,
            error=('The app installed, but the managed restart request was rejected. Run amplifier-unified service restart.' if rejected else None),
            detail=('Waiting for a healthy restarted host. New work remains paused.' if rejected else
                    'Restart request confirmation was interrupted. Waiting for a healthy restarted host; new work remains paused. If it does not reconnect, run amplifier-unified service restart.'))
        if isinstance(error,asyncio.CancelledError):raise
        return
    marker.update(requestStatus='accepted')
    facts={'durationMs':round((time.monotonic()-started)*1000),**getattr(result,'diagnostic_facts',{})}
    manager.diagnostics.record('service-restart-request','accepted',commandId=command_id,**facts)
    await manager.publish(phase='activating',pendingRestart=marker,error=None,
        detail='The service manager accepted the restart request. Waiting for the updated host to become ready…')


def restart_arguments(manager, executable):
    """Preserve effective CLI overrides without rewriting persistent deployment settings."""
    from .deployment import load_server_config, validate_server
    config=getattr(manager.service,'server_config',None)
    config=validate_server(config) if config is not None else load_server_config(manager.home)
    options=[executable,'--no-open','--port',str(manager.service.port),'--data-dir',str(manager.home),
             '--workspace',manager.service.default_workspace,'--session-ttl',str(config['session_ttl_seconds'])]
    for bind in config['bind']:options.extend(['--bind',bind])
    for origin in config['public_origins']:options.extend(['--public-origin',origin])
    if config['tls']['method']!='none':
        for field in ('cert','key'):
            if config['tls'][field]:options.extend(['--tls-'+field,config['tls'][field]])
    return options
