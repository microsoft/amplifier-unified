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
import time
from urllib.parse import urlsplit
import uuid
from .host.config import write_private


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


async def process(*args, cwd=None, env=None, timeout=90):
    proc = await asyncio.create_subprocess_exec(*map(str,args), cwd=cwd, env=env,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, start_new_session=os.name != 'nt')
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout)
    except BaseException:
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
        raise
    if proc.returncode:
        # Subprocess stderr can contain configured credentials and source URLs.
        raise RuntimeError('Command failed; source could not be checked or prepared')
    return stdout.decode(errors='replace').strip()


class UpdateManager:
    def __init__(self, service):
        self.service = service
        self.home = service.data_dir
        self.directory = self.home / 'updates'
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = asyncio.Lock()
        self.inventory = []
        self.task = None
        self.closed = False
        state = service.state.setdefault('updates', {})
        restarted=state.get('pendingRestart') or {}
        if restarted.get('version')==__import__('amplifier_web').__version__:
            state.update(phase='installed',pendingRestart=None,pendingApp=None,appAvailable=False,
                         installedAt=time.time(),detail='Application update installed and restarted successfully.')
        elif state.get('phase') in {'checking','staging','validating','activating'}:
            state.update(phase='interrupted', detail='The update was interrupted; installed sources were not replayed.')
        state.setdefault('phase', 'idle')
        state.setdefault('items', [])
        application=state.get('application',{})
        from .app_updates import version_tuple
        latest=version_tuple(application.get('latest'))
        current=version_tuple(__import__('amplifier_web').__version__)
        if latest and current and latest<=current:
            application={**application,'current':__import__('amplifier_web').__version__,'status':'current'}
            state.update(application=application,appAvailable=False)
            state['items']=[application if row.get('id')=='application' else row for row in state['items']]
            state['available']=sum(row.get('status')=='update' for row in state['items'])
        state['items']=group_sources(state['items'])
        state['available']=sum(row.get('status')=='update' for row in state['items'])
        state.setdefault('lastCheck', None)
        state['release'] = active_release(self.home).get('current')
        state['canRollback'] = 'previous' in active_release(self.home)

    async def publish(self, **values):
        async with self.service.lock:
            self.service.state['updates'].update(values)
            self.service._publish()

    def busy(self):
        state = self.service.state
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
                dirty = await process('git','status','--porcelain','--untracked-files=no',cwd=root,timeout=10)
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
        try:
            await getattr(self, action)()
        except asyncio.CancelledError: raise
        except Exception:
            await self.publish(phase='error',error='The update operation failed; no conversation work was replayed.')

    async def check(self):
        if self.lock.locked() or self.service.state['updates'].get('pendingApp') or self.service.state['updates'].get('pendingRelease'): return
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
                app_available=application.get('status')=='update'
                await self.publish(phase='available' if app_available or any(r['status']=='update' for r in rows) else 'checked',
                    items=public+self.protected_items()+[application],application=application,appAvailable=app_available,
                    available=sum(r['status']=='update' for r in public)+int(app_available),
                    lastCheck=time.time(), detail='Check complete. Pins and failed checks are listed separately.')
            except Exception:
                await self.publish(phase='error', error='Update check failed. Your installed sources are unchanged.')

    async def app(self):
        if self.lock.locked():return
        from .app_updates import stage,activate
        async with self.lock:
            if not self.service.state['updates'].get('pendingApp'):
                await stage(self)
        await activate(self)

    async def install(self):
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
            stage=self.directory/'releases'/release
            stage.mkdir(parents=True,mode=0o700)
            source=foundation_home(self.home)
            try:
                await self.publish(phase='staging',detail='Preparing an isolated copy of the ecosystem…',error=None)
                await asyncio.to_thread(shutil.copytree,source,stage/'foundation',symlinks=True)
                for name in ('config','routing'):
                    if (self.home/name).exists(): await asyncio.to_thread(shutil.copytree,self.home/name,stage/name)
                for config_file in (stage/'config').rglob('*.yaml'):
                    config_file.write_text(config_file.read_text().replace(str(source),str(stage/'foundation')))
                registry=stage/'foundation/registry.json'
                if registry.exists(): registry.write_text(registry.read_text().replace(str(source),str(stage/'foundation')))
                for row in candidates:
                    target=stage/'foundation'/row['path']
                    if not target.resolve().is_relative_to((stage/'foundation').resolve()): raise ValueError('Invalid cache path')
                    current=await process('git','rev-parse','HEAD',cwd=target)
                    dirty=await process('git','status','--porcelain','--untracked-files=no',cwd=target)
                    if dirty or current!=row['current']: raise ValueError('Source changed since check')
                    await self.publish(detail='Downloading '+row['label']+'…')
                    await process('git','-c','core.hooksPath=/dev/null','fetch','--depth=1',row['url'],row['latest'],cwd=target)
                    await process('git','-c','core.hooksPath=/dev/null','checkout','--detach',row['latest'],cwd=target)
                    meta=target/'.amplifier_cache_meta.json'
                    data=json.loads(meta.read_text());data.update(commit=row['latest'],cached_at=time.strftime('%Y-%m-%dT%H:%M:%S'))
                    meta.write_text(json.dumps(data))
                await self.publish(phase='validating',detail='Validating bundles and modules in a separate runtime…')
                await self.validate(stage,release)
                write_private(stage/'validated.json',json.dumps({'createdAt':time.time(),'sources':len(candidates),'hostVersion':__import__('amplifier_web').__version__}))
                await self.publish(phase='staged',pendingRelease=release,detail='Update validated; waiting for conversations and calls to be idle.')
            except asyncio.CancelledError: raise
            except Exception:
                await self.publish(phase='error',pendingRelease=None,error='The staged update failed validation. Current sources remain active; no conversation work was replayed.')
                return
        await self.activate()

    async def validate(self,stage,release):
        from .runtime import RuntimeManager
        command=RuntimeManager()._command(release=release)
        command[-1]=str(Path(__file__).with_name('update_probe.py'))
        state=self.service.get_state()
        configs={(s['workspace'],s['bundle']) for s in state['sessions']}
        configs.add((state['settings']['workspace'],state['settings']['bundle']))
        env={**os.environ,'AMPLIFIER_WEB_HOME':str(stage),'AMPLIFIER_UNIFIED_RELEASE':'',
            'UV_OVERRIDE':str(Path(__file__).parent/'runtime_deps/compatibility.txt')}
        for workspace,bundle in sorted(configs):
            await process(*command,workspace,bundle,env=env,timeout=900)

    async def activate(self, rollback=False):
        if self.lock.locked(): return
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
                items=[] if rollback else [{**row, **({'status':'current','current':row['latest']} if row.get('status')=='update' else {})} for row in self.service.state['updates'].get('items',[])]
                await self.publish(phase='installed',release=target,pendingRelease=None,canRollback=True,items=items,
                    available=0,installedAt=time.time(),detail='Previous ecosystem restored. Automatic installation is now off.' if rollback else 'Update installed. Conversations will resume with the new ecosystem.')
                self.inventory=[]
                write_private(self.directory/'inventory.json','[]')
            except Exception:
                await self.publish(phase='error',error='Could not activate update; retry when the app is idle.')

    async def rollback(self):
        await self.activate(rollback=True)

    async def tick(self):
        settings=self.service.state['settings'].get('updates',{})
        state=self.service.state['updates']
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
            except Exception: await self.publish(phase='error',error='Background update failed; current installation remains available.')
            await asyncio.sleep(60)

    async def close(self):
        self.closed=True
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task,return_exceptions=True)
