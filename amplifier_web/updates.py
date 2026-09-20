"""App-owned ecosystem updates: read-only checks, isolated staging, atomic promotion.

Only mutable Git sources in our Foundation cache are refreshable. Version/SHA
pins, local worktrees, the patched engine and host libraries stay explicit.
"""
from __future__ import annotations
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import time
from urllib.parse import urlsplit
import uuid
from .host.config import write_private


def work_paused(state):
    updates = state.get('updates', {})
    return updates.get('phase') == 'activating' or bool(updates.get('pendingRestart'))


def active_release(home):
    path = Path(home) / 'updates' / 'active.json'
    value = json.loads(path.read_text()) if path.exists() else {}
    for field in ('current','previous'):
        identity = value.get(field)
        if identity is not None and (not isinstance(identity,str) or not re.fullmatch(r'[a-f0-9]{32}', identity)):
            raise ValueError('Invalid ecosystem release identity')
    return value


def foundation_home(home):
    home = Path(home)
    identity = active_release(home).get('current')
    return home / 'updates' / 'releases' / identity / 'foundation' if identity else home / 'foundation'


def safe_label(url):
    parsed = urlsplit(url)
    return (parsed.hostname or 'Git source') + '/' + parsed.path.strip('/').removesuffix('.git')


def group_sources(items):
    """Collapse identical cached checkouts for display; installation keeps every path."""
    groups={}
    for item in items:
        row={k:v for k,v in item.items() if k not in {'path','url','eligible'}}
        if row.get('kind')!='bundle / module':
            groups[row['id']]=row
            continue
        key=tuple(row.get(k) for k in ('label','ref','current','latest','status'))
        if key in groups:
            groups[key]['cacheCopies']+=row.get('cacheCopies',1)
        else:
            row['id']='source:'+hashlib.sha256(json.dumps(key).encode()).hexdigest()[:20]
            row['cacheCopies']=row.get('cacheCopies',1)
            groups[key]=row
    return list(groups.values())


def pinned(ref):
    return bool(re.fullmatch(r'[0-9a-fA-F]{7,40}', ref) or re.match(r'^(refs/tags/|v?\d+\.)', ref))


async def process(*args, cwd=None, env=None, timeout=90, raw=False):
    started=time.monotonic()
    proc = await asyncio.create_subprocess_exec(*map(str,args), cwd=cwd, env=env,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, start_new_session=os.name != 'nt')
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout)
    except BaseException as error:
        if proc.returncode is None:
            try:
                if os.name != 'nt':
                    import signal
                    os.killpg(proc.pid, signal.SIGTERM)
                else: proc.terminate()
            except ProcessLookupError:
                pass
            try: await asyncio.wait_for(proc.wait(), 5)
            except TimeoutError:
                try:
                    if os.name != 'nt': os.killpg(proc.pid, signal.SIGKILL)
                    else: proc.kill()
                except ProcessLookupError:
                    pass
                await proc.wait()
        if isinstance(error,TimeoutError):
            from .update_diagnostics import CommandTimeout
            raise CommandTimeout(time.monotonic()-started) from None
        raise
    if proc.returncode:
        from .update_diagnostics import CommandFailure,command_facts
        raise CommandFailure(command_facts(stdout,stderr,proc.returncode,time.monotonic()-started))
    if raw:return stdout
    from .update_diagnostics import CommandOutput,command_facts
    return CommandOutput(stdout.decode(errors='replace').strip(),command_facts(stdout,stderr,proc.returncode,time.monotonic()-started))


async def cache_changes(root):
    """Return protected changes and proven cache artifacts, without editing files.

    Imported caches used to flatten symlinks, and some upstreams track generated
    bytecode. Only their unstaged, exactly verified forms may be restored later,
    inside an isolated staging copy. Untracked files are never removed.
    """
    root = Path(root).resolve()
    records = (await process('git','status','--porcelain=v1','-z','--untracked-files=no',cwd=root,timeout=10,raw=True)).split(b'\0')
    protected, artifacts = [], []

    async def head_entry(path):
        value = await process('git','--literal-pathspecs','ls-tree','-z','HEAD','--',path,cwd=root,timeout=10,raw=True)
        entries = value.split(b'\0')
        if len(entries)!=2 or not entries[0]:return None
        metadata, name = entries[0].split(b'\t',1)
        mode, kind, identity = metadata.decode('ascii').split()
        return (mode,identity) if kind=='blob' and os.fsdecode(name)==path else None

    def regular(path, mode=None):
        candidate = root/path
        try:
            if not candidate.resolve(strict=True).is_relative_to(root):return False
            if any(parent.is_symlink() for parent in [candidate,*candidate.parents] if parent!=root and parent.is_relative_to(root)):return False
            current = candidate.stat().st_mode
            return stat.S_ISREG(current) and (mode is None or bool(current&stat.S_IXUSR)==(mode=='100755'))
        except (OSError,ValueError):return False

    def bytecode_header(value):
        return len(value)>=16 and value[2:4]==b'\r\n' and int.from_bytes(value[4:8],'little')&~3==0

    index = 0
    while index<len(records):
        record=records[index];index+=1
        if not record:continue
        change=record[:2].decode('ascii');path=os.fsdecode(record[3:])
        # Renames/copies have a second NUL-delimited name, even with whitespace.
        if 'R' in change or 'C' in change:index+=1
        if change not in {' M',' T'}:
            protected.append(path);continue
        entry=await head_entry(path)
        if not entry or not regular(path):
            protected.append(path);continue
        mode,identity=entry
        generated=False
        if change==' M' and mode in {'100644','100755'} and regular(path,mode) and '__pycache__' in Path(path).parts and re.fullmatch(r'.+\.cpython-\d+(?:\.opt-\d+)?\.pyc',Path(path).name):
            original=await process('git','cat-file','blob',identity,cwd=root,timeout=10,raw=True)
            with (root/path).open('rb') as file:header=file.read(16)
            generated=bytecode_header(original) and bytecode_header(header)
        elif change==' T' and mode=='120000':
            link=os.fsdecode(await process('git','cat-file','blob',identity,cwd=root,timeout=10,raw=True))
            # A direct, tracked regular target only: no outside paths or chains.
            target=Path(os.path.normpath(str(Path(path).parent/link)))
            if not Path(link).is_absolute() and '..' not in target.parts and regular(target):
                target_entry=await head_entry(target.as_posix())
                if target_entry and target_entry[0] in {'100644','100755'} and regular(target,target_entry[0]) and regular(path,target_entry[0]):
                    hashes=(await process('git','hash-object','--no-filters','--',path,str(target),cwd=root,timeout=10)).splitlines()
                    generated=hashes==[target_entry[1],target_entry[1]]
        (artifacts if generated else protected).append(path)
    return protected,artifacts


class UpdateManager:
    def __init__(self, service):
        self.service = service
        self.home = service.data_dir
        self.directory = self.home / 'updates'
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = asyncio.Lock()
        self.inventory = []
        self.task = None
        self.readiness_task = None
        self.closed = False
        from .update_readiness import running_identity
        self.running_identity = running_identity()
        state = service.state.setdefault('updates', {})
        restarted=state.get('pendingRestart') or {}
        if restarted:
            state.update(phase='activating', pendingApp=None,
                         detail='Checking that the restarted server is serving the installed release…')
        elif state.get('phase') in {'checking','staging','validating','activating'}:
            if state.get('phase')=='activating':state['pendingApp']=None
            state.update(phase='interrupted', detail='The update was interrupted; installed sources were not replayed.')
        state.setdefault('phase', 'idle')
        state.setdefault('items', [])
        from .app_updates import version_tuple,application_state
        application={**application_state(),**state.get('application',{}),'current':__import__('amplifier_web').__version__}
        from .release_notes import history
        application['releaseNotes']=history(application.get('releaseNotes',[]),application.get('latest') if version_tuple(application.get('latest')) else None)
        application['runningRevision'] = self.running_identity['revision']
        state['application']=application
        latest=version_tuple(application.get('latest'))
        current=version_tuple(__import__('amplifier_web').__version__)
        if latest and current and latest<=current:
            application={**application,'current':__import__('amplifier_web').__version__,'status':'current','releaseBehind':latest<current,
                         'detail':'This installation is newer than the latest published release. Updates follow published releases, not the main branch.' if latest<current else 'The latest published application release is installed.'}
            state.update(application=application,appAvailable=False)
            state['items']=[application if row.get('id')=='application' else row for row in state['items']]
            state['available']=sum(row.get('status')=='update' for row in state['items'])
        state['items']=group_sources(state['items'])
        state['available']=sum(row.get('status')=='update' for row in state['items'])
        state.setdefault('lastCheck', None)
        state['release'] = active_release(self.home).get('current')
        state['canRollback'] = 'previous' in active_release(self.home)
        from .update_diagnostics import UpdateDiagnostics
        self.diagnostics=UpdateDiagnostics(self)
        from .update_readiness import valid_target
        if restarted and not valid_target(restarted):
            state['error'] = 'The saved restart receipt is incomplete. The running release cannot be confirmed from that receipt; review the update details.'
        service._save()

    async def confirm_readiness(self, health, expected=None):
        from .update_readiness import confirm_readiness
        return await confirm_readiness(self, health, expected=expected)

    def awaiting_restart(self):
        from .update_readiness import recovery_candidate
        # A legacy erased-marker receipt must not permanently prevent repairs
        # after an unrelated manual upgrade. Only its active health check gates
        # another update; an actual pending handoff remains gated until verified.
        return bool(self.service.state['updates'].get('pendingRestart') or
                    (self.readiness_task and not self.readiness_task.done() and recovery_candidate(self)))

    async def publish(self, **values):
        async with self.service.lock:
            self.service.state['updates'].update(values)
            self.service._publish()

    def busy(self):
        state = self.service.state
        if any(not task.done() for task in getattr(self.service,'smart_tool_tasks',())):return True
        if any(op.get('status') in {'queued','running'} for op in state.get('smartTools',{}).get('operations',[])):return True
        if any(request.get('status') in {'queued','sending'} for request in state.get('feedback',{}).get('requests',[])):return True
        if state.get('voice',{}).get('status') not in {None,'disconnected','idle','ended','error'}:
            return True
        for session in state['sessions']:
            if session.get('configurationBusy') or session['status'] in {'working','starting','ready','stopping'}: return True
            if any(w.get('status') in {'starting','running','stopping','queued'} or (w.get('persistent') and w.get('status') == 'idle') for w in session.get('workers',[])): return True
        return False

    async def inventory_sources(self):
        base = foundation_home(self.home)
        rows = []
        for meta in sorted((base/'cache').rglob('.amplifier_cache_meta.json')):
            # Each cache may include nested skills copies. All are app-owned;
            # skip symlinked external worktrees and malformed metadata.
            root = meta.parent
            if root.is_symlink() or not root.resolve().is_relative_to(base.resolve()): continue
            try:
                data = json.loads(meta.read_text())
                url, ref = data['git_url'], data.get('ref') or 'HEAD'
                parsed = urlsplit(url)
                if parsed.scheme not in {'https','http','ssh'} or not parsed.hostname: continue
                relative = str(root.relative_to(base))
                identity = hashlib.sha256(relative.encode()).hexdigest()[:20]
                current = await process('git','rev-parse','HEAD',cwd=root,timeout=10)
                dirty, _ = await cache_changes(root)
                rows.append({'id':identity,'label':safe_label(url),'ref':ref,'current':current,
                    'status':'local_changes' if dirty else 'pinned' if pinned(ref) else 'not_checked',
                    'path':relative,'url':url,'kind':'bundle / module','eligible':not dirty and not pinned(ref)})
            except (ValueError, KeyError, RuntimeError, TimeoutError):
                rows.append({'id':hashlib.sha256(str(root).encode()).hexdigest()[:20],
                    'label':'Unrecognized cached source','status':'check_failed','eligible':False,'kind':'source'})
        return rows

    def protected_items(self):
        rows = []
        rows.extend({'id':'host:'+name,'label':name,'kind':'host library','status':'pinned',
            'detail':'Compatibility pin; updated with a tested application release.'}
            for name in ['amplifier-core','amplifier-foundation','amplifier-module-loop-streaming','amplifier-module-loop-live'])
        return rows

    async def command(self, action):
        if self.awaiting_restart():return
        previous_error=self.service.state['updates'].get('error')
        try:
            await getattr(self, action)()
        except asyncio.CancelledError: raise
        except Exception as error:
            if self.service.state['updates'].get('phase')=='error' and self.service.state['updates'].get('error') and self.service.state['updates']['error']!=previous_error:return
            from .update_diagnostics import exception_type
            last=self.diagnostics.state.get('latest',{})
            phase=last.get('phase',action)
            if last.get('status')!='failed':self.diagnostics.record(phase,'failed',errorType=exception_type(error))
            await self.publish(phase='activating' if self.service.state['updates'].get('pendingRestart') else 'error',error='Update failed during '+phase.replace('-',' ')+'. Review the diagnostic receipt; no conversation work was replayed.')

    async def check(self):
        if self.lock.locked() or self.awaiting_restart() or self.service.state['updates'].get('pendingApp') or self.service.state['updates'].get('pendingRelease'): return
        async with self.lock:
            await self.publish(phase='checking', lastAttempt=time.time(), detail='Checking configured ecosystem sources…', error=None)
            try:
                rows = await self.inventory_sources()
                semaphore = asyncio.Semaphore(5)
                remote_tasks = {}
                async def remote(url, ref):
                    async with semaphore:
                        pattern = ref if ref in {'HEAD'} or ref.startswith('refs/') else 'refs/heads/'+ref
                        output = await process('git','ls-remote',url,pattern,timeout=35)
                        sha = output.split()[0] if output else ''
                        if not re.fullmatch('[a-f0-9]{40}',sha): raise ValueError('Remote ref unavailable')
                        return sha
                async def check_row(row):
                    if not row.get('eligible'): return
                    key = (row['url'],row['ref'])
                    task = remote_tasks.setdefault(key, None)
                    if task is None:
                        task = remote_tasks[key] = asyncio.create_task(remote(*key))
                    try:
                        row['latest'] = await task
                        row['status'] = 'update' if row['latest'] != row['current'] else 'current'
                    except (ValueError, RuntimeError, TimeoutError): row['status']='check_failed'
                await asyncio.gather(*(check_row(row) for row in rows))
                self.inventory = rows
                write_private(self.directory/'inventory.json',json.dumps(rows))
                public = group_sources(rows)
                from .app_updates import check as check_application
                application=await check_application()
                previous=self.service.state['updates'].get('application',{})
                if application.get('status')=='check_failed':
                    from .release_notes import history
                    from .app_updates import version_tuple
                    application['releaseNotes']=history(previous.get('releaseNotes',[]),previous.get('latest') if version_tuple(previous.get('latest')) else None)
                    application['releaseNotesWarning']='The update check failed. Showing saved release notes; check again to confirm the latest release.'
                elif application.get('releaseNotesWarning') and application.get('revision')==previous.get('revision'):
                    from .release_notes import history
                    application['releaseNotes']=history(previous.get('releaseNotes',[]),application.get('latest'))
                    application['releaseNotesWarning']='Release notes could not be refreshed. Showing saved release history.'
                application['runningRevision'] = self.running_identity['revision']
                app_available=application.get('status')=='update'
                await self.publish(phase='available' if app_available or any(r['status']=='update' for r in rows) else 'checked',
                    items=public+self.protected_items()+[application],application=application,appAvailable=app_available,
                    available=sum(r['status']=='update' for r in public)+int(app_available),
                    lastCheck=time.time(), detail='Check complete. Pins and failed checks are listed separately.')
            except Exception:
                await self.publish(phase='error', error='Update check failed. Your installed sources are unchanged.')

    async def app(self):
        if self.lock.locked() or self.awaiting_restart():return
        from .app_updates import stage,activate
        async with self.lock:
            if not self.service.state['updates'].get('pendingApp'):
                await stage(self)
        await activate(self)

    async def install(self):
        if self.awaiting_restart():return
        if self.service.state['updates'].get('pendingApp'):
            from .app_updates import activate
            await activate(self)
            return
        if self.service.state['updates'].get('pendingRelease'):
            await self.activate()
            return
        if self.service.state['updates'].get('appAvailable'):
            await self.app()
            return
        if self.lock.locked(): return
        async with self.lock:
            if not self.inventory:
                path=self.directory/'inventory.json'
                self.inventory=json.loads(path.read_text()) if path.exists() else []
            candidates=[r for r in self.inventory if r.get('status')=='update' and r.get('eligible')]
            if not candidates:
                await self.publish(detail='Check for updates before installing. No eligible updates are available.')
                return
            release=uuid.uuid4().hex
            self.diagnostics.begin('ecosystem',release)
            self.diagnostics.record('ecosystem-stage','started')
            stage=self.directory/'releases'/release
            stage.mkdir(parents=True,mode=0o700)
            source=foundation_home(self.home)
            try:
                await self.publish(phase='staging',detail='Preparing an isolated copy of the ecosystem…',error=None)
                await self.diagnostics.run('ecosystem-copy',asyncio.to_thread,shutil.copytree,source,stage/'foundation',symlinks=True)
                for name in ('config','routing'):
                    if (self.home/name).exists(): await asyncio.to_thread(shutil.copytree,self.home/name,stage/name)
                from .session_files import amplifier_home
                shared_stage=stage/'shared-config'
                shared_stage.mkdir(mode=0o700)
                for name in ('settings.yaml','keys.env','routing'):
                    original=amplifier_home()/name
                    if original.is_dir():await asyncio.to_thread(shutil.copytree,original,shared_stage/name)
                    elif original.is_file():await asyncio.to_thread(shutil.copy2,original,shared_stage/name)
                for config_file in (stage/'config').rglob('*.yaml'):
                    config_file.write_text(config_file.read_text().replace(str(source),str(stage/'foundation')))
                registry=stage/'foundation/registry.json'
                if registry.exists(): registry.write_text(registry.read_text().replace(str(source),str(stage/'foundation')))
                for row in candidates:
                    target=stage/'foundation'/row['path']
                    if not target.resolve().is_relative_to((stage/'foundation').resolve()): raise ValueError('Invalid cache path')
                    current=await process('git','rev-parse','HEAD',cwd=target)
                    dirty,artifacts=await cache_changes(target)
                    if dirty or current!=row['current']: raise ValueError('Source changed since check')
                    if artifacts:
                        # Restore only verified tracked artifacts in this copy.
                        # Rechecking here also protects edits made after check.
                        await process('git','--literal-pathspecs','-c','core.hooksPath=/dev/null','restore','--source=HEAD','--worktree','--',*artifacts,cwd=target)
                    await self.publish(detail='Downloading '+row['label']+'…')
                    await self.diagnostics.run('ecosystem-fetch',process,'git','-c','core.hooksPath=/dev/null','fetch','--depth=1',row['url'],row['latest'],cwd=target)
                    await self.diagnostics.run('ecosystem-checkout',process,'git','-c','core.hooksPath=/dev/null','checkout','--detach',row['latest'],cwd=target)
                    meta=target/'.amplifier_cache_meta.json'
                    data=json.loads(meta.read_text());data.update(commit=row['latest'],cached_at=time.strftime('%Y-%m-%dT%H:%M:%S'))
                    meta.write_text(json.dumps(data))
                await self.publish(phase='validating',detail='Validating bundles and modules in a separate runtime…')
                await self.validate(stage,release)
                write_private(stage/'validated.json',json.dumps({'createdAt':time.time(),'sources':len(candidates),'hostVersion':__import__('amplifier_web').__version__}))
                self.diagnostics.clear_failure()
                self.diagnostics.record('ecosystem-stage','succeeded')
                await self.publish(phase='staged',pendingRelease=release,detail='Update validated; waiting for conversations and calls to be idle.')
            except asyncio.CancelledError: raise
            except Exception as error:
                from .update_diagnostics import exception_type
                last=self.diagnostics.state.get('latest',{})
                phase=last.get('phase','ecosystem-stage')
                if last.get('status')!='failed':self.diagnostics.record(phase,'failed',errorType=exception_type(error))
                await self.publish(phase='error',pendingRelease=None,error='Ecosystem update failed during '+phase.replace('-',' ')+'. Current sources remain active; no conversation work was replayed.')
                return
        await self.activate()

    async def validate(self,stage,release):
        from .runtime import RuntimeManager
        command=RuntimeManager()._command(release=release)
        command[-1]=str(Path(__file__).with_name('update_probe.py'))
        state=self.service.get_state()
        # Browsing historical CLI projects does not opt their old bundles into
        # this application's update validation or mount missing workspaces.
        configs={(s['workspace'],s['bundle']) for s in state['sessions']
                 if not s.get('historyManaged') and s.get('workspace') and s.get('bundle')}
        configs.add((state['settings']['workspace'],state['settings']['bundle']))
        env={**os.environ,'AMPLIFIER_WEB_HOME':str(stage),'AMPLIFIER_HOME':str(stage/'shared-config'),'AMPLIFIER_UNIFIED_RELEASE':'',
            'UV_OVERRIDE':str(Path(__file__).parent/'runtime_deps/compatibility.txt')}
        for workspace,bundle in sorted(configs):
            await self.diagnostics.run('ecosystem-probe',process,*command,workspace,bundle,env=env,timeout=900)

    async def activate(self, rollback=False):
        if self.lock.locked() or self.awaiting_restart(): return
        async with self.lock:
            if self.closed: return
            pointer=active_release(self.home)
            target=pointer.get('previous') if rollback else self.service.state['updates'].get('pendingRelease')
            if target is not None and (not isinstance(target,str) or not re.fullmatch(r'[a-f0-9]{32}',target)):
                raise ValueError('Invalid ecosystem release identity')
            if rollback and 'previous' not in pointer: return
            if not rollback and not target: return
            if target and not (self.directory/'releases'/target/'validated.json').exists():
                raise ValueError('This ecosystem release has not passed validation')
            if target:
                marker=json.loads((self.directory/'releases'/target/'validated.json').read_text())
                if marker.get('hostVersion')!=__import__('amplifier_web').__version__:
                    await self.validate(self.directory/'releases'/target,target)
                    marker['hostVersion']=__import__('amplifier_web').__version__
                    write_private(self.directory/'releases'/target/'validated.json',json.dumps(marker))
            async with self.service.lock:
                if self.busy():
                    self.service.state['updates'].update(detail='Waiting for active work and calls to finish. Try rollback again when idle.' if rollback else 'Update ready; it will activate when work and calls finish.')
                    self.service._publish()
                    return
                self.service.state['updates'].update(phase='activating',detail='Switching ecosystem version…')
                self.service._publish()
            try:
                # New work is gated during this short phase. Old sessions remain
                # durable; only idle worker processes are closed, then resumed normally.
                if self.service.runtime: await self.service.runtime.close()
                write_private(self.directory/'active.json',json.dumps({'current':target,'previous':pointer.get('current'),'at':time.time()}))
                if rollback:
                    async with self.service.lock:
                        self.service.state['settings']['updates']['autoInstall'] = False
                items=[row for row in self.service.state['updates'].get('items',[]) if row.get('id')=='application'] if rollback else [{**row, **({'status':'current','current':row['latest']} if row.get('status')=='update' and row.get('id')!='application' else {})} for row in self.service.state['updates'].get('items',[])]
                await self.publish(phase='installed',release=target,pendingRelease=None,canRollback=True,items=items,error=None,
                    available=sum(row.get('status')=='update' for row in items),installedAt=time.time(),detail='Previous ecosystem restored. Automatic installation is now off.' if rollback else 'Update installed. Conversations will resume with the new ecosystem.')
                self.diagnostics.clear_failure()
                self.diagnostics.record('ecosystem-activation','succeeded')
                self.inventory=[]
                write_private(self.directory/'inventory.json','[]')
            except Exception as error:
                from .update_diagnostics import exception_type
                self.diagnostics.record('ecosystem-activation','failed',errorType=exception_type(error))
                await self.publish(phase='error',error='Could not activate the ecosystem update; review its diagnostic receipt before retrying.')

    async def rollback(self):
        await self.activate(rollback=True)

    async def tick(self):
        settings=self.service.state['settings'].get('updates',{})
        state=self.service.state['updates']
        if self.awaiting_restart():return
        if state.get('pendingApp'):
            from .app_updates import activate
            await activate(self)
            return
        if state.get('pendingRelease'):
            await self.activate()
            return
        if settings.get('autoCheck',True) and time.time()-max(state.get('lastCheck') or 0,state.get('lastAttempt') or 0)>=settings.get('intervalHours',24)*3600:
            await self.check()
        state=self.service.state['updates']
        if settings.get('autoInstall',False) and state.get('phase')=='available' and state.get('available',0):
            await self.install()

    async def loop(self):
        await asyncio.sleep(3)
        while not self.closed:
            try:
                await self.tick()
            except asyncio.CancelledError: raise
            except Exception as error:
                from .update_diagnostics import exception_type
                last=self.diagnostics.state.get('latest',{})
                if last.get('status') not in {'failed','interrupted'}:
                    self.diagnostics.record('background-update','failed',errorType=exception_type(error))
                phase=self.diagnostics.state['latest']['phase']
                await self.publish(phase='activating' if self.service.state['updates'].get('pendingRestart') else 'error',error='Background update failed during '+phase.replace('-',' ')+'. Review its diagnostic receipt; no work was replayed.')
            await asyncio.sleep(60)

    async def close(self):
        self.closed=True
        if self.readiness_task:
            self.readiness_task.cancel()
            await asyncio.gather(self.readiness_task,return_exceptions=True)
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task,return_exceptions=True)
