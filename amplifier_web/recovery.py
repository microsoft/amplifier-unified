"""Private full backups and reversible app-owned reset operations."""
import asyncio
import copy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import tarfile
import time
import uuid

PARTS={'runtime':['runtime'],'cache':['foundation/cache','updates/releases','updates/active.json'],'settings':['config','routing','bundles'],'conversations':['sessions']}

CONFIGURATION_FILES = ('configuration.json', 'effective-configuration.json')


def _read_reset_configuration(path, home):
    """Read an existing plan, bounded by its own size, not a reset-only limit."""
    if os.name == 'nt':
        from .windows_files import _component, directory
        _component(path.name)
        with directory(path.parent, owned_root=home) as pinned:
            # The public small-config reader has a 1 MiB ceiling. Keep the same
            # pinned parents, ACL-checked existing handle and native reader,
            # but bound this already persisted plan by that handle's size.
            pinned._check()
            handle = pinned._existing(path.name)
            try:
                info = pinned._api.info(handle, pinned.path / path.name, directory=False)
                size = (info.size_high << 32) | info.size_low
                content = pinned._api.read(handle, limit=size)
            finally:
                pinned._api.close(handle)
            if len(content) != size:
                raise ValueError('Saved configuration changed during reset.')
            return content
    from .staged_sources import owned_path
    owned_path(path, home)
    with path.open('rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError('Reset configuration must be a private regular file: ' + str(path.relative_to(home)))
        content = stream.read(info.st_size + 1)
    if len(content) != info.st_size:
        raise ValueError('Saved configuration changed during reset.')
    return content


def _retiring_reference(value, roots):
    """Recognize one schema-owned local source/resource path without rewriting it."""
    if not isinstance(value, str):
        return False
    if value.startswith('file:'):
        from urllib.parse import parse_qs, urlsplit
        from urllib.request import url2pathname
        uri = urlsplit(value)
        if uri.netloc not in ('', 'localhost'):
            return False
        value = url2pathname(uri.path)
        subdirectory = parse_qs(uri.fragment).get('subdirectory', [])
        if len(subdirectory) == 1:
            value = str(Path(value) / subdirectory[0])
    path = Path(os.path.normpath(value.split('#', 1)[0]))
    if value.startswith('~/'):
        path = path.expanduser()
    return path.is_absolute() and any(path.is_relative_to(root) for root in roots)


def _reset_plan_references(plan):
    """Only executable declarations and explicit context-file maps own paths here."""
    if not isinstance(plan, dict):
        return
    from .host.components import agent_roster, declarations
    yield from (row.get('source') for row in declarations(plan))

    def resources(node):
        context = node.get('context')
        if isinstance(context, dict):
            yield from context.values()
        for child in agent_roster(node.get('agents', {})).values():
            if isinstance(child, dict):
                yield from resources(child)
    yield from resources(plan)


def prepare_reset_configurations(home, targets):
    """Precompute legacy plan conversion before losing managed source provenance.

    No writes, downloads, mounting, request changes or cache inventory. Callers
    own quiescence and rollback. Explicit context files cannot be reconstructed
    from module provenance. Module config, prompt text and other opaque values
    are not executable source declarations and remain their module's concern.
    """
    home = Path(home)
    sessions = home / 'sessions'
    if not sessions.exists() and not sessions.is_symlink():
        return []
    from .staged_sources import owned_path
    owned_path(sessions, home)
    roots = tuple(home / name for name in targets)
    changes = []
    for directory in sorted(sessions.iterdir()):
        if not directory.is_dir():
            continue
        for name in (*CONFIGURATION_FILES, 'pending-configuration.json'):
            path = directory / name
            if not path.exists() and not path.is_symlink():
                continue
            before = _read_reset_configuration(path, home)
            plan = json.loads(before)
            if not any(_retiring_reference(value, roots) for value in _reset_plan_references(plan)):
                continue
            relative = str(path.relative_to(home))
            if name == 'pending-configuration.json':
                # Success/cancellation owners unlink this queue file. Their
                # retained configuration-operations records are history, not
                # reset inputs. A remaining queue is not proof of completion.
                raise ValueError('Reset cannot retire sources used by pending configuration: ' + relative +
                                 '. Resolve the original operation first; its request was not changed.')
            from .host.components import declarations, portable_module_sources
            converted = portable_module_sources(plan, home)
            # Do not change managed sources which this reset is not retiring.
            for old, new in zip(declarations(plan), declarations(converted), strict=True):
                if 'source' in old and not _retiring_reference(old['source'], roots):
                    new['source'] = old['source']
            if any(_retiring_reference(value, roots) for value in _reset_plan_references(converted)):
                raise ValueError('Reset cannot preserve ' + relative +
                                 ': a retiring source is missing, changed or unverified, or an explicit '
                                 'context resource still uses a retiring path. No cache was retired.')
            after = json.dumps(converted, indent=2).encode('utf-8')
            changes.append((path, before, after))
    return changes


def stage_reset_configurations(home, targets, destination):
    """Fresh-install CLI bridge: private prepared output, not a second journal."""
    from .host.config import write_private
    home, destination = Path(home), Path(destination)
    changes = prepare_reset_configurations(home, targets)
    entries = []
    for path, before, after in changes:
        relative = path.relative_to(home)
        write_private(destination / relative, after, owned_root=home)
        entries.append({'path': relative.as_posix(), 'before': hashlib.sha256(before).hexdigest()})
    write_private(destination / 'prepared.json', json.dumps(entries), owned_root=home)


def bulk_sessions(service, *, include_native=False):
    """Explicit backup/reset/cleanup scope, independent of the resident set.

    Normal navigation must use the directory's bounded queries instead. These
    user-requested bulk operations need all local owners, including their cold
    presentation references, without admitting them or starting any runtime.
    Untouched native history is excluded from private backup scope.
    """
    from .state_records import load
    from .session_projection import hydrate
    durable = load(service.db) or {'sessions': []}
    context = {'sessions': durable.get('sessions', [])}
    hydrate(service.data_dir, context, service.db)
    rows = {row['id']: row for row in context['sessions']}
    if include_native:
        for row in service.directory.select(limit=-1):
            rows.setdefault(row['id'], row)
    # Active owners may have changes that have not been published yet.
    rows.update((row['id'], row) for row in service.state['sessions'])
    return list(rows.values())


def _backup_files(data_dir, session_paths, publishing=None, publishing_captures=(), operation_id=None):
    folder=data_dir/'backups'/(operation_id or uuid.uuid4().hex)
    if operation_id is None:
        folder.mkdir(parents=True,mode=0o700)
    database=folder/'app.sqlite3'
    # This thread owns its connections; never use the event-loop connection here.
    with sqlite3.connect(f"file:{data_dir / 'app.sqlite3'}?mode=ro",uri=True) as source, sqlite3.connect(database) as target:
        # Hold one WAL read snapshot. Frequent UI writes must not restart a
        # multi-GB incremental backup indefinitely.
        source.execute('BEGIN')
        source.execute('SELECT id FROM state LIMIT 1').fetchone()
        source.backup(target,pages=256)
    database.chmod(0o600)
    if publishing is not None:
        publishing.snapshot(folder/'publishing')
    for identity, capture in publishing_captures:
        capture.snapshot(folder/'publishing-captures'/identity)
    archive=folder/'private-state.tar.gz'
    with tarfile.open(archive,'w:gz') as output:
        output.add(database,arcname='app.sqlite3')
        if publishing is not None:
            output.add(folder/'publishing',arcname='publishing',recursive=True)
        if publishing_captures:
            output.add(folder/'publishing-captures',arcname='publishing-captures',recursive=True)
        operations=data_dir/'operations.sqlite3'
        if operations.exists():
            snapshot=folder/'operations.sqlite3'
            with sqlite3.connect(f"file:{operations}?mode=ro",uri=True) as source, sqlite3.connect(snapshot) as target:
                source.execute('BEGIN')
                source.execute('SELECT id FROM operations LIMIT 1').fetchone()
                source.backup(target,pages=256)
            snapshot.chmod(0o600)
            output.add(snapshot,arcname='operations.sqlite3')
        original=data_dir/'recall.sqlite3'
        if original.exists():
            snapshot=folder/'recall.sqlite3'
            with sqlite3.connect(f"file:{original}?mode=ro",uri=True) as source, sqlite3.connect(snapshot) as target:
                source.backup(target,pages=256)
            snapshot.chmod(0o600)
            output.add(snapshot,arcname='recall.sqlite3')
        for name in ('config','routing','bundles','sessions','artifacts','smart-tools/work'):
            path=data_dir/name
            if path.exists():output.add(path,arcname=name,recursive=True)
        for path, name in session_paths:
            if path.exists():output.add(path,arcname=name,recursive=True)
        for name in ('events.sqlite3','index.sqlite3'):
            original=data_dir/'diagnostics'/name
            if original.exists():
                snapshot=folder/name
                with sqlite3.connect(f"file:{original}?mode=ro",uri=True) as source, sqlite3.connect(snapshot) as target:
                    source.backup(target,pages=256)
                snapshot.chmod(0o600)
                output.add(snapshot,arcname='diagnostics/'+name)
        config=data_dir/'diagnostics/config.json'
        if config.exists():output.add(config,arcname='diagnostics/config.json')
    archive.chmod(0o600)
    return {'backup':str(archive),'phase':'ready','detail':'Private backup includes app-owned shared session files, artifacts, configuration and saved credentials. Keep it private; it is not encrypted.'}


async def backup(service, *, publishing_locked=False, operation_id=None):
    if not publishing_locked:
        async with service.publishing.lock:
            return await backup(service, publishing_locked=True, operation_id=operation_id)
    started=False
    try:
        async with service.lock:
            if getattr(service,'backup_in_progress',False):raise ValueError('A backup is already running.')
            service.backup_in_progress=True
            started=True
            service.state['maintenance']={'phase':'backing-up','detail':'Creating a private backup in the background.'}
            service._publish_changes(globals={'maintenance'})
            from .session_files import capture_dir, sessions_dir, validate_id
            from .shared_settings import settings_paths
            from .session_files import amplifier_home
            paths={(amplifier_home()/name,'shared-config/'+name) for name in ('settings.yaml','keys.env','routing','bundles')}
            sessions=[s for s in bulk_sessions(service)
                      if not s.get('historyManaged') and s.get('workspace')]
            workspaces={service.default_workspace,*[s['workspace'] for s in sessions]}
            from .session_files import project_slug
            for workspace in workspaces:
                for scope,path in settings_paths(workspace).items():
                    if scope!='global':paths.add((path,'workspace-settings/'+project_slug(workspace)+'/'+path.name))
            def include(workspace, identity):
                try:validate_id(identity)
                except ValueError:return
                # Resolve without SessionStore construction: backup must not
                # create directories merely because a project was registered.
                path=sessions_dir(workspace)/identity
                if path.is_symlink():raise ValueError('Session directories cannot be symbolic links')
                name='shared-projects/'+path.parent.parent.name+'/sessions/'+path.name
                paths.add((path,name))
                capture=capture_dir(workspace,identity)
                if capture != path/'context-intelligence':
                    paths.add((capture,'relocated-captures/'+path.parent.parent.name+'/sessions/'+identity+'/context-intelligence'))
            # The rebuildable navigation index can describe thousands of CLI
            # sessions. Only sessions used by this app expand the backup scope.
            for session in sessions:
                include(session['workspace'],session.get('runtimeSessionId') or session['id'])
                for worker in session.get('workers',[]):
                    if worker.get('id'):include(session['workspace'],worker['id'])
        task=asyncio.create_task(asyncio.to_thread(_backup_files,service.data_dir,paths,service.publishing.store,tuple(service.publishing.captures.items()),operation_id))
        try:return await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise
    except (Exception, asyncio.CancelledError):
        if started:
            async with service.lock:
                service.state['maintenance'] = {'phase':'error', 'detail':'The backup did not finish. Review the error and retry when ready.'}
                service._publish_changes(globals={'maintenance'})
        raise
    finally:
        if started:service.backup_in_progress=False

async def _settled_thread(function, *args):
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        # Repeated shutdown cancellation must not detach a still-running file
        # worker or release its process lease early.
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
        task.result()
        raise


async def cleanup(manager):
    """Explicit retry, without repeating a reset or changing success order."""
    from .reset_retention import retry_cleanup
    result = await _settled_thread(retry_cleanup, manager.service.data_dir)
    detail = ('Older reset recovery cleanup finished.' if result['cleanupComplete'] else
              'Reset succeeded; older-backup cleanup incomplete. Retry maintenance.resetCleanup after resolving the reported errors.')
    if result.get('reviewRequired'):
        detail += ' Unresolved attempts or ambiguous legacy recovery copies remain; review them explicitly.'
    await manager.publish(maintenance={**manager.service.state.get('maintenance', {}),
        'resetRetention': result, 'detail': detail})
    return result


async def reset(manager,args,command_id=None):
    from .reset_retention import Retention
    service = manager.service
    if not args.get('apply'):
        async with service.publishing.lock:
            return await _reset(manager, args)
    # Validate before creating ownership state (including rejected confirmations).
    parts = args.get('parts', ['runtime'])
    if not parts or set(parts)-set(PARTS):raise ValueError('Choose runtime, cache, settings or conversations')
    if args.get('confirmation') != 'RESET':raise ValueError('Type RESET to apply the selected reset')
    if 'conversations' in parts and any(service.publishing.owns_records(row['id']) for row in bulk_sessions(service)):
        raise ValueError('Conversations own retained publishing releases and receipts. Archive them to preserve publishing controls and ownership.')
    owner = Retention(service.data_dir)
    try:
        await _settled_thread(owner.acquire)
    except BaseException:
        owner.close()
        raise
    operation = None
    try:
        operation = await _settled_thread(owner.begin, 'in-app')
        # Reset must not discard ownership while a publishing action is starting.
        async with service.publishing.lock:
            await _reset(manager, args, operation)
        # The final outcome must be committed as well as reset stores. The reset
        # owns this terminal transition so _command must not make a second one.
        await manager.command_transition('maintenance.reset', args, command_id, 'ready')
    except BaseException:
        try:
            if operation:
                await _settled_thread(owner.fail, operation)
        finally:
            owner.close()
        raise
    try:
        try:
            await _settled_thread(owner.succeed, operation)
            result = await _settled_thread(owner.cleanup)
        except Exception as error:
            result = {'cleanupComplete': False, 'errors': [str(error)], 'retainedOperationId': operation}
        except asyncio.CancelledError:
            # The action outcome is already durable; cancellation stops further
            # delivery, not the successful reset or its completed thread work.
            return True
        detail = ('Selected app data reset. The latest reset recovery set is retained privately; older successful sets were removed.'
                  if result['cleanupComplete'] else 'Reset succeeded; older-backup cleanup incomplete. Retry maintenance.resetCleanup after resolving the reported errors.')
        if result.get('reviewRequired'):
            detail += ' Unresolved attempts or ambiguous legacy recovery copies remain; review them explicitly.'
        # These are cleanup results, outside the runtime rollback handler.
        try:
            await manager.publish(maintenance={**service.state['maintenance'], 'resetRetention': result, 'detail': detail})
        except (Exception, asyncio.CancelledError):
            # Filesystem cleanup evidence is durable; a publication failure must
            # not replace the already-saved successful reset receipt with error.
            return True
    finally:
        owner.close()
    return True


async def _reset(manager,args,operation=None):
    service=manager.service
    parts=args.get('parts',['runtime'])
    if not parts or set(parts)-set(PARTS):raise ValueError('Choose runtime, cache, settings or conversations')
    targets=[p for part in parts for p in PARTS[part]]
    if not args.get('apply'):
        await manager.publish(maintenance={'resetPreview':{'parts':parts,'paths':targets},'detail':'Reset only affects this app’s data. A private backup and retained originals are created before applying. A successful reset replaces the previous reset recovery point.'})
        return
    if args.get('confirmation')!='RESET':raise ValueError('Type RESET to apply the selected reset')
    if 'conversations' in parts and any(service.publishing.owns_records(row['id']) for row in bulk_sessions(service)):
        raise ValueError('Conversations own retained publishing releases and receipts. Archive them to preserve publishing controls and ownership.')
    from .runtime_retention import DEFAULT_RETENTION
    async with service.runtime_lifecycle():
        async with service.lock:
            if service.closed:raise RuntimeError('The runtime host is closing.')
            if service.update_manager and (service.update_manager.busy() or service.update_manager.lock.locked()):raise ValueError('Finish active work and updates before resetting')
            candidate=service.runtime_candidate(retention=DEFAULT_RETENTION if 'settings' in parts else None)
            service.state.setdefault('updates',{})['phase']='activating'
            service._publish_changes(globals={'updates'})
        try:
            await service.replace_runtime(candidate)
            for setup in manager.workspace_setup_managers.values():await setup.close()
            manager.workspace_setup_managers.clear()
            service._configuration_snapshots = {}
            for key in ("setup_manager", "global_setup_manager"):
                setup=getattr(manager,key,None)
                if setup:await setup.close();setattr(manager,key,None)
            changes = (await _settled_thread(prepare_reset_configurations, service.data_dir, targets)
                       if 'cache' in parts and 'conversations' not in parts else [])
            result=await backup(service, publishing_locked=True, operation_id=operation)
            async with service.lock:
                sessions=bulk_sessions(service, include_native=True) if 'conversations' in parts else []
                retained=service.data_dir/'backups'/('reset-'+operation)
                from .host.config import write_private
                for path, before, _ in changes:
                    if _read_reset_configuration(path, service.data_dir) != before:
                        raise ValueError('Saved configuration changed during reset; no cache was retired.')
                moves, written = [], []
                try:
                    for path, before, after in changes:
                        # Record before writing: an atomic replace can succeed
                        # before its writer reports an error.
                        written.append((path, before))
                        write_private(path, after, owned_root=service.data_dir)
                    for name in targets:
                        path=service.data_dir/name
                        if path.is_symlink():raise ValueError('Refusing to reset a linked app directory')
                        if path.exists():
                            target=retained/name;target.parent.mkdir(parents=True,exist_ok=True)
                            path.rename(target)
                            moves.append((path, target))
                except BaseException:
                    for original, saved in reversed(moves):
                        saved.rename(original)
                    for path, before in reversed(written):
                        write_private(path, before, owned_root=service.data_dir)
                    raise
                # Catalog/history/settings writes below may commit before
                # publication raises. File-only rollback is no longer safe.
                if 'conversations' in parts:
                    for session in sessions:
                        service.history.hide_session(session)
                    service.forget_session_records(row['id'] for row in sessions)
                    service.state.update(sessions=[],selectedSessionId=None,runtimeControl={},sessionConfiguration={},history=[])
                    # Commands own idempotent effect receipts, including this
                    # reset's queued identity. Reset chats, never replay rights.
                if 'settings' in parts:
                    from .updates import DEFAULT_CHECK_INTERVAL_HOURS
                    service.state['settings']['updates']={'autoCheck':True,'autoInstall':False,'intervalHours':DEFAULT_CHECK_INTERVAL_HOURS}
                    service._shared_preferences_stamp=None
                    service._refresh_shared_preferences()
                    service.state.update(setup={},bundles={},bundleDiscovery={},permissions={})
                    service.state['notificationSettings']=manager.notifications.public()
                    # Dormant owners have no mounted runtime to retire; their
                    # next start reads the reset settings normally.
                    for session in service.state['sessions']:
                        if not session.get('historyManaged'):session['configurationPending']=True
                if 'cache' in parts:
                    service.state['updates'].update(release=None,pendingRelease=None,canRollback=False,items=[],available=0)
                    service.update_manager.inventory=[]
                service.state['updates'].update(phase='idle',pendingApp=None)
                result.update(retained=str(retained),resetOperationId=operation,detail='Selected app data reset. Backup and original files are retained privately; your workspaces and CLI data were untouched.')
                service.state['maintenance']=result
                service._publish_full(reason='Explicit reset of selected application stores')
        except BaseException:
            await service.discard_runtime(candidate)
            await manager.publish(updates={**service.state.get('updates',{}),'phase':'error','error':'Reset did not complete; retained originals are in backups.'})
            raise
