"""App-owned ecosystem updates: read-only checks, isolated staging, atomic promotion.

Mutable Git sources in the Foundation cache and worker environment are
refreshable. User-supplied pins and local worktrees remain explicit choices.
"""
from __future__ import annotations
import asyncio
from collections.abc import Mapping
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import time
import tomllib
from types import SimpleNamespace
from urllib.parse import urlsplit
import uuid
from .host.config import write_private


def work_paused(state):
    updates = state.get('updates', {})
    from .app_replacement import pending
    return updates.get('phase') == 'activating' or bool(updates.get('pendingRestart')) or pending(updates)


def active_release(home):
    path = Path(home) / 'updates' / 'active.json'
    try:
        value = json.loads(path.read_text())
    except FileNotFoundError:
        value = {}
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
        key = (*key, row.get('usage'), tuple(row.get('usageEvidence', [])), row.get('updateTier'))
        if key in groups:
            groups[key]['cacheCopies']+=row.get('cacheCopies',1)
        else:
            row['id']='source:'+hashlib.sha256(json.dumps(key).encode()).hexdigest()[:20]
            row['cacheCopies']=row.get('cacheCopies',1)
            groups[key]=row
    return list(groups.values())


def pinned(ref):
    return bool(re.fullmatch(r'[0-9a-fA-F]{7,40}', ref) or re.match(r'^(refs/tags/|v?\d+\.)', ref))


def source_key(url, ref):
    """Compare repository/ref identity, never infer that main and master agree."""
    parsed = urlsplit(url)
    if parsed.scheme not in {'https', 'http', 'ssh'} or not parsed.hostname or not isinstance(ref, str) or not ref:
        raise ValueError('Unsupported Git source')
    return (parsed._replace(path=parsed.path.rstrip('/').removesuffix('.git'), fragment='').geturl(),
            ref.removeprefix('refs/heads/'))


def configured_sources(service):
    """Positive configuration evidence only, NOT a reachability/garbage collector.

    Registry entries resolve selected names; their mere presence proves nothing.
    Dynamic/transitive dependencies and local bundle contents remain unknown.
    Never prepare a bundle, import credentials or mutate configuration here.
    """
    from .host.config import read_config
    from .shared_settings import SettingsReadCache
    import yaml
    sources, incomplete = {}, False
    settings_cache = SettingsReadCache()
    issues = getattr(service, 'source_issues', None)
    def issue(reason, *, workspace=None, session_id=None, reference=None, historical=False):
        nonlocal incomplete
        incomplete = True
        if issues is None:
            return
        # Report identities and locations, never arbitrary settings or exception
        # contents (which may include credentials).
        if isinstance(reference, str) and reference.startswith(('git+', 'https:', 'http:', 'ssh:')):
            reference = safe_label(reference.removeprefix('git+'))
        owner = next((session for session in state['sessions'] if session_id and
                      (session.get('runtimeSessionId') or session.get('nativeIdentity') or session['id']) == session_id
                      and session.get('workspace') == workspace), None)
        row = {'reason': reason, 'historical': bool(historical and owner and owner.get('historyManaged') and owner['id'] != state.get('selectedSessionId')), **({'appSessionId': owner['id']} if owner else {}), **({'workspace': str(workspace)} if workspace else {}),
               **({'sessionId': session_id} if session_id else {}),
               **({'reference': str(reference)[:300]} if reference else {})}
        if row not in issues:
            if len(issues) < 50:
                issues.append(row)
            elif not row['historical']:
                # Old selections cannot crowd an active configuration error
                # out of the bounded report.
                replace = next((index for index, item in enumerate(issues) if item.get('historical')), None)
                if replace is not None:
                    issues[replace] = row
    state, home = service.state, service.data_dir
    selections = {(state['settings']['workspace'], state['settings']['bundle'], None)}
    # Native child/legacy history can be viewable without a resumable identity.
    # Its workspace is still inspected below; it is not a broken root selection.
    selections.update((s['workspace'], s['bundle'], s.get('runtimeSessionId') or s.get('nativeIdentity') or s['id'])
                      for s in state['sessions']
                      if not (s.get('historyManaged') and s.get('historyReadOnlyReason')))
    for workspace in state.get('workspaces', []):
        # Native history retains an unavailable, pathless workspace placeholder.
        # It is not a source-selection error, unlike malformed workspace rows.
        if (isinstance(workspace, Mapping)
                and isinstance(workspace.get('nativeProject'), str)
                and workspace.get('nativeProject')
                and 'path' in workspace and workspace['path'] is None
                and workspace.get('available') is False):
            continue
        path = workspace.get('path') if isinstance(workspace, Mapping) else None
        if not isinstance(path, str) or not path.strip():
            incomplete = True
            continue
        selections.add((path, None, None))
    try:
        path = foundation_home(home)/'registry.json'
        registry = json.loads(path.read_text()).get('bundles', {}) if path.exists() else {}
        if not isinstance(registry, dict):raise ValueError('Invalid registry')
    except (OSError, ValueError, AttributeError):
        registry = {}
        issue('The bundle registry could not be read. Open Bundles to refresh its catalog.')

    for workspace, selected, session_id in selections:
        try:
            # Missing registrations and saved sessions remain available for
            # history. Their unavailable directories are not configuration errors.
            if not workspace:continue
            if not Path(workspace).expanduser().is_dir():continue
            config = read_config(workspace, home=home, session_id=session_id, settings_cache=settings_cache)
            registrations = {name: row['uri'] for name, row in registry.items()
                             if isinstance(row, dict) and isinstance(row.get('uri'), str)}
            configured = dict(config.registrations)
            if 'foundation' in registry:configured.pop('foundation', None)
            registrations.update(configured)

            def resolve(reference, evidence, seen=frozenset()):
                nonlocal incomplete
                if not isinstance(reference, str) or not reference or reference in seen:
                    issue('A source alias is empty or circular.', workspace=workspace, session_id=session_id, reference=reference)
                    return
                if reference.startswith('git+'):
                    parsed = urlsplit(reference[4:])
                    path, separator, ref = parsed.path.rpartition('@')
                    url = parsed._replace(path=path if separator else parsed.path, fragment='').geturl()
                    sources.setdefault(source_key(url, ref if separator else 'HEAD'), set()).add(evidence)
                    return
                # Match the host's local-before-registry preference. Local files
                # may contain arbitrary includes; don't guess their dependencies.
                from .host.bundle_paths import local_bundle_path
                if local_bundle_path(config, reference) is not None:
                    return
                namespace = reference.split(':', 1)[0]
                replacement = config.resolve_source(reference) or registrations.get(namespace)
                if replacement:
                    resolve(replacement, evidence, seen | {reference})
                else:
                    issue('This bundle name is not registered. Choose an available bundle in this conversation, or restore its source in workspace settings.', workspace=workspace, session_id=session_id, reference=reference, historical=evidence == 'Selected bundle')

            # Do not walk added/registered bundle lists: those are catalogs.
            resolve(selected or config.active_bundle, 'Selected bundle')
            for reference in config.app_bundles:
                resolve(reference, 'Enabled app bundle')
            for reference in config.module_sources.values():
                resolve(reference, 'Module source configuration')

            def configured_git_values(value):
                # Module config can declare skill sources (and other plugins'
                # Git inputs). Only explicit Git URIs are positive evidence.
                if isinstance(value, dict):
                    for child in value.values():configured_git_values(child)
                elif isinstance(value, list):
                    for child in value:configured_git_values(child)
                elif isinstance(value, str) and value.startswith('git+'):
                    resolve(value, 'Git input in module configuration')
            configured_git_values(config.settings.get('config', {}))
        except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError, yaml.YAMLError):
            issue('Workspace or conversation settings could not be read. Review the settings files for this workspace.', workspace=workspace, session_id=session_id, reference=selected)
    return sources, incomplete


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


# Content identity of the reviewed wiki-weaver hook, NOT a dependency/revision pin.
# Unknown hook implementations remain protected until separately reviewed.
_WIKI_VERSION_HOOK_SHA256 = 'd0733aa7fecaadf307fdeb42dd202c4fec1a8255e56b24bb23777cbd003620f3'
_WIKI_VERSION_HEADER = b'''"""Single source of truth for the wiki-weaver package version.

Kept as a leaf module with no imports so that any submodule can safely
import __version__ without risk of triggering a circular import through
the wiki_weaver package __init__.

This value is baked in at wheel-build time by the git-version hatchling
build hook (see hatch_build.py at the repo root) -- it is the commit date
+ short SHA of the exact commit this wheel was built from, not the build
date. Do not hand-edit; it is overwritten on the next build.
"""

'''


async def cache_changes(root):
    """Return protected changes and proven cache artifacts, without editing files.

    Imported caches used to flatten symlinks, and some upstreams track generated
    bytecode. Wiki-weaver's reviewed build hook also bakes a version into source.
    Only unstaged, exactly verified generated forms may be restored later,
    inside an isolated staging copy. Untracked files are never removed.
    """
    root = Path(root).resolve()
    records = (await process('git','--no-optional-locks','status','--porcelain=v1','-z','--untracked-files=no',cwd=root,timeout=10,raw=True)).split(b'\0')
    protected, artifacts = [], []

    async def head_entry(path, revision='HEAD'):
        value = await process('git','--literal-pathspecs','ls-tree','-z',revision,'--',path,cwd=root,timeout=10,raw=True)
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

    async def wiki_generated_version():
        # Never execute/import a cached hook or evaluate Python source. Verify its
        # exact reviewed bytes and reproduce only that implementation's output.
        version_path = 'wiki_weaver/_version.py'
        originals = {}
        try:
            captured = await process('git','rev-parse','--verify','HEAD^{commit}',cwd=root,timeout=10,raw=True)
            if not re.fullmatch(rb'(?:[0-9a-f]{40}|[0-9a-f]{64})\n',captured):return False
            revision = captured[:-1].decode('ascii')
            for name in (version_path, 'hatch_build.py', 'pyproject.toml'):
                entry = await head_entry(name, revision)
                if not entry or entry[0] not in {'100644','100755'} or not regular(name,entry[0]):return False
                mode, identity = entry
                indexed = await process('git','--literal-pathspecs','ls-files','--stage','-z','--',name,cwd=root,timeout=10,raw=True)
                if indexed != f'{mode} {identity} 0\t{name}\0'.encode():return False
                original = await process('git','cat-file','blob',identity,cwd=root,timeout=10,raw=True)
                if name != version_path and (root/name).read_bytes() != original:return False
                originals[name] = original
            if hashlib.sha256(originals['hatch_build.py']).hexdigest() != _WIKI_VERSION_HOOK_SHA256:return False
            project = tomllib.loads(originals['pyproject.toml'].decode('utf-8'))
            if project['project']['name'] != 'wiki-weaver':return False
            if project['build-system'] != {'requires':['hatchling'], 'build-backend':'hatchling.build'}:return False
            if project['tool']['hatch']['build']['hooks']['custom'] != {'path':'hatch_build.py'}:return False
            original = originals[version_path]
            if not original.startswith(_WIKI_VERSION_HEADER):return False
            if not re.fullmatch(rb'__version__ = "[0-9]{4}\.[0-9]{2}\.[0-9]{2}-[0-9a-f]{4,64}"\n',original[len(_WIKI_VERSION_HEADER):]):return False
            # Match the hook's actual Git semantics (including core.abbrev), not
            # an assumed seven-character prefix or a wall-clock/author date.
            # Bind all awaited reads to one commit; a moving HEAD cannot mix
            # one commit's tree/date with another commit's abbreviation.
            date = await process('git','log','-1','--format=%cd','--date=format:%Y.%m.%d',revision,cwd=root,timeout=10,raw=True)
            sha = await process('git','rev-parse','--short',revision,cwd=root,timeout=10,raw=True)
            if not re.fullmatch(rb'[0-9]{4}\.[0-9]{2}\.[0-9]{2}\n',date):return False
            if not re.fullmatch(rb'[0-9a-f]{4,64}\n',sha) or not captured[:-1].startswith(sha[:-1]):return False
            expected = _WIKI_VERSION_HEADER + b'__version__ = "' + date[:-1] + b'-' + sha[:-1] + b'"\n'
            if (root/version_path).read_bytes() != expected:return False
            return await process('git','rev-parse','--verify','HEAD^{commit}',cwd=root,timeout=10,raw=True) == captured
        except (OSError, ValueError, KeyError, TypeError, RuntimeError, TimeoutError):
            return False

    index = 0
    while index<len(records):
        record=records[index];index+=1
        if not record:continue
        change=record[:2].decode('ascii');path=os.fsdecode(record[3:])
        # Renames/copies have a second NUL-delimited name, even with whitespace.
        if 'R' in change or 'C' in change:index+=1
        if change not in {' M',' T'}:
            protected.append(path);continue
        if path=='wiki_weaver/_version.py':
            generated=change==' M' and await wiki_generated_version()
            (artifacts if generated else protected).append(path)
            continue
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
        from .update_readiness import running_identity,valid_target
        self.running_identity = running_identity()
        state = service.state.setdefault('updates', {})
        from .app_replacement import pending as replacement_pending
        # An older process may have died after replacing its files but before
        # recording a restart target. Preserve uncertainty, never infer rollback.
        if not replacement_pending(state) and not valid_target(state.get('pendingRestart')) and state.get('phase') == 'activating' and state.get('pendingApp'):
            state['pendingReplacement'] = {'unqualified': True, 'detail': 'An older app replacement was interrupted without complete qualification evidence.'}
        restarted=state.get('pendingRestart')
        restart_repair=restarted is not None and not valid_target(restarted)
        if replacement_pending(state):
            if restart_repair:
                state.update(pendingRestart=None, error='The saved restart receipt was invalid. No restart success was inferred; replacement uncertainty remains fenced.')
            state.update(phase='activating', pendingApp=None,
                         detail='Application replacement may have changed this installation. Work remains paused until a healthy new host proves the qualified app and dependencies. If evidence is incomplete, repair and qualify the installation before reopening admission.')
        elif restart_repair:
            state.update(phase='interrupted',pendingRestart=None,pendingApp=None,
                         error='The saved restart receipt was invalid and has been retired. No restart success was inferred.',
                         detail='The interrupted restart was not acknowledged. Review the diagnostic receipt before retrying updates.')
        elif restarted:
            state.update(phase='activating', pendingApp=None,
                         detail='Checking that the restarted server is serving the installed release…')
        elif state.get('phase') in {'checking','staging','validating','activating'}:
            if state.get('phase')=='activating':state['pendingApp']=None
            state.update(phase='interrupted', detail='The update was interrupted; installed sources were not replayed.')
        state.setdefault('phase', 'idle')
        from .app_features import reconcile_requests
        reconcile_requests(state)
        state.setdefault('items', [])
        from .app_updates import version_tuple,application_state
        application={**application_state(),**state.get('application',{}),'current':__import__('amplifier_web').__version__}
        from .release_notes import history
        application['releaseNotes']=history(application.get('releaseNotes',[]),application.get('latest') if version_tuple(application.get('latest')) else None)
        application['runningRevision'] = self.running_identity['revision']
        state['application']=application
        latest=version_tuple(application.get('latest'))
        current=version_tuple(__import__('amplifier_web').__version__)
        if latest and current and latest<=current and not (latest==current and application.get('componentUpdates')):
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
        if restart_repair:
            self.diagnostics.record('restart-repair','failed',errorType='ValueError',preserve_last_failure=True)
        else:
            service._save()

    async def confirm_readiness(self, health, expected=None):
        from .update_readiness import confirm_readiness
        return await confirm_readiness(self, health, expected=expected)

    def awaiting_restart(self):
        from .update_readiness import recovery_candidate
        # A legacy erased-marker receipt must not permanently prevent repairs
        # after an unrelated manual upgrade. Only its active health check gates
        # another update; an actual pending handoff remains gated until verified.
        from .app_replacement import pending
        return bool(pending(self.service.state['updates']) or self.service.state['updates'].get('pendingRestart') or
                    (self.readiness_task and not self.readiness_task.done() and recovery_candidate(self)))

    async def publish(self, **values):
        async with self.service.lock:
            self.service.state['updates'].update(values)
            self.service._publish()

    def busy(self):
        state = self.service.state
        pending = getattr(getattr(self.service, 'runtime', None), 'has_pending_operations', None)
        if pending and pending():return True
        if any(not task.done() for task in getattr(self.service,'smart_tool_tasks',())):return True
        if any(op.get('status') in {'queued','running'} for op in state.get('smartTools',{}).get('operations',[])):return True
        if any(request.get('status') in {'queued','sending'} for request in state.get('feedback',{}).get('requests',[])):return True
        if any(request.get('status') in {'queued','sending'} for request in state.get('feedback',{}).get('followups',[])):return True
        if state.get('voice',{}).get('status') not in {None,'disconnected','idle','ended','error'}:
            return True
        for session in state['sessions']:
            if session.get('configurationBusy') or session['status'] in {'working','starting','stopping'}: return True
            # Ready describes a mounted runtime, including control-only use.
            # A cold-start send also passes through ready before delivery, so
            # keep its already-admitted execution turn protected.
            if session['status'] == 'ready' and any(turn.get('phase') == 'running' for turn in session.get('execution',{}).get('turns',[])):return True
            if any(w.get('status') in {'starting','running','stopping','queued'} or (w.get('persistent') and w.get('status') == 'idle') for w in session.get('workers',[])): return True
        return False

    async def inventory_sources(self):
        base = foundation_home(self.home)
        # Capture only selection metadata before yielding. The history catalog
        # can change while filesystem/settings reads run outside the event loop.
        state = self.service.state
        snapshot = SimpleNamespace(data_dir=self.home, state={
            'selectedSessionId': state.get('selectedSessionId'),
            'settings': {key: state['settings'][key] for key in ('workspace', 'bundle')},
            'sessions': [{key: row[key] for key in (
                'id', 'workspace', 'bundle', 'runtimeSessionId', 'nativeIdentity',
                'historyManaged', 'historyReadOnlyReason') if key in row}
                for row in state['sessions']],
            'workspaces': [{key: row[key] for key in ('nativeProject', 'path', 'available') if key in row}
                           if isinstance(row, Mapping) else row
                           for row in state.get('workspaces', [])],
        })
        snapshot.source_issues = []
        configured, incomplete = await asyncio.to_thread(configured_sources, snapshot)
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
                evidence = sorted(configured.get(source_key(url, ref), ()))
                dirty, _ = await cache_changes(root)
                rows.append({'id':identity,'label':safe_label(url),'ref':ref,'current':current,
                    'status':'local_changes' if dirty else 'pinned' if pinned(ref) else 'not_checked',
                    'usage':'configured' if evidence else 'unknown','usageEvidence':evidence,
                    **({'detail':'Tracked source changes are preserved and block automatic updates. Builds can also modify tracked files (including version stamps); without verified provenance these changes are not discarded.'} if dirty else {}),
                    'path':relative,'url':url,'kind':'bundle / module','eligible':not dirty and not pinned(ref)})
            except (ValueError, KeyError, RuntimeError, TimeoutError):
                rows.append({'id':hashlib.sha256(str(root).encode()).hexdigest()[:20],
                    'label':'Unrecognized cached source','status':'check_failed','eligible':False,'kind':'source',
                    'usage':'unknown','usageEvidence':[]})
        active_issues = [issue for issue in snapshot.source_issues if not issue.get('historical')]
        historical_issues = [issue for issue in snapshot.source_issues if issue.get('historical')]
        if incomplete and (active_issues or not snapshot.source_issues):
            rows.append({'id':'source-configuration','label':'Source configuration','status':'check_failed',
                'eligible':False,'kind':'configuration',
                'detail':'Some saved bundle selections or settings could not be resolved. This affects usage labels, not cached-source update checks. Review the affected sources below.',
                'sourceIssues': active_issues})
        if historical_issues:
            rows.append({'id': 'historical-source-configuration', 'label': 'Older conversation settings', 'status': 'historical',
                'eligible': False, 'kind': 'history', 'sourceIssues': historical_issues,
                'detail': 'These older conversations use bundles that are no longer registered. Their history is kept. Choose an available bundle if you resume one; cached-source updates are unaffected.'})
        from .runtime_environment import update_inventory
        rows.extend(await update_inventory(self.home))
        from .update_sequence import classify
        return classify(self.home, rows)

    async def command(self, action, args=None, command_id=None):
        if self.awaiting_restart() and action != 'featureInstall':return
        previous_error=self.service.state['updates'].get('error')
        try:
            if action == 'featureInstall':
                await self.featureInstall(args['feature'], args['hostInstanceId'], command_id)
            else:
                await getattr(self, action)()
        except asyncio.CancelledError: raise
        except Exception as error:
            # Feature requests own a durable per-request result. A rejected
            # concurrent request must not change an active update's work gate.
            if action == 'featureInstall':return
            if self.service.state['updates'].get('phase')=='error' and self.service.state['updates'].get('error') and self.service.state['updates']['error']!=previous_error:return
            from .update_diagnostics import exception_type
            last=self.diagnostics.state.get('latest',{})
            phase=last.get('phase',action)
            if last.get('status')!='failed':self.diagnostics.record(phase,'failed',errorType=exception_type(error))
            await self.publish(phase='activating' if self.service.state['updates'].get('pendingRestart') else 'error',error='Update failed during '+phase.replace('-',' ')+'. Review the diagnostic receipt; no conversation work was replayed.')

    async def check(self, *, tier='application', install=False):
        if self.lock.locked() or self.awaiting_restart() or self.service.state['updates'].get('pendingApp') or self.service.state['updates'].get('pendingRelease'): return
        from .update_sequence import summary
        async with self.lock:
            state = self.service.state['updates']
            sequence = ({'stage': 'application', 'install': install,
                         'included': summary([], checked=False), 'other': summary([], checked=False)}
                        if tier == 'application' else {**state.get('sequence', {}), 'stage': tier, 'install': install})
            sequence.pop('nextStage', None)
            await self.publish(phase='checking', sequence=sequence, lastAttempt=time.time(),
                               detail='Checking the application release…' if tier == 'application' else 'Checking included components…' if tier == 'included' else 'Checking other components…', error=None)
            try:
                application = state.get('application', {})
                if tier == 'application':
                    from .app_updates import check as check_application
                    application = await check_application()
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
                    app_available = application.get('status') == 'update'
                    # Do not inventory or resolve dependencies against an app
                    # version that is about to be replaced.
                    self.inventory = []
                    write_private(self.directory/'inventory.json', '[]')
                    await self.publish(application=application, appAvailable=app_available,
                                       items=[application], available=int(app_available))
                    if app_available or application.get('status') != 'current':
                        await self.publish(phase='available' if app_available else 'error', lastCheck=time.time(),
                            error=None if app_available else application.get('detail') or 'The application release could not be checked. Try again before checking components.',
                            detail='App update available. Included components will be checked after the new app restarts.' if app_available else 'Component checks are waiting for the application check.')
                        return
                    tier = 'included'
                rows = await self.inventory_sources()
                semaphore = asyncio.Semaphore(5)
                remote_tasks = {}
                async def remote(url, ref):
                    async with semaphore:
                        pattern = ref if ref == 'HEAD' or ref.startswith('refs/') else 'refs/heads/'+ref
                        output = await process('git','ls-remote',url,pattern,timeout=35)
                        sha = output.split()[0] if output else ''
                        if not re.fullmatch('[a-f0-9]{40}',sha): raise ValueError('Remote ref unavailable')
                        return sha
                async def check_row(row):
                    if not row.get('eligible') or row.get('kind') == 'runtime environment': return
                    key = (row['url'],row['ref'])
                    task = remote_tasks.setdefault(key, None)
                    if task is None:
                        task = remote_tasks[key] = asyncio.create_task(remote(*key))
                    try:
                        row['latest'] = await task
                        row['status'] = 'update' if row['latest'] != row.get('current') else 'current'
                    except (ValueError, RuntimeError, TimeoutError): row['status']='check_failed'
                checked = []
                for stage in (['included', 'other'] if tier == 'included' else ['other']):
                    selected = [row for row in rows if row.get('updateTier', 'other') == stage]
                    sequence['stage'] = stage
                    await self.publish(sequence={**sequence}, detail='Checking included components…' if stage == 'included' else 'Checking other components…')
                    await asyncio.gather(*(check_row(row) for row in selected))
                    checked.extend(selected)
                    sequence.update(stage=stage)
                    sequence[stage] = summary(group_sources(selected))
                    if sequence[stage]['available'] or (stage == 'included' and sequence[stage]['issues']):break
                self.inventory = checked
                write_private(self.directory/'inventory.json',json.dumps(checked))
                public = group_sources(checked)
                available = sum(row.get('status') == 'update' for row in public)
                blocked = sequence['stage'] == 'included' and sequence['included']['issues']
                if not available and not blocked:sequence['stage'] = 'complete';sequence['install'] = False
                await self.publish(phase='error' if blocked else 'available' if available else 'checked',
                    sequence=sequence, items=public+[application], application=application, appAvailable=False,
                    available=available, lastCheck=time.time(),
                    error='An included component could not be checked or has local changes. Review its details before continuing.' if blocked else None,
                    detail='Included components will be installed before checking the remaining sources.' if sequence['stage']=='included' and available else 'Component check complete.')
            except Exception:
                await self.publish(phase='error', error='Update check failed. Your installed sources are unchanged.')

    async def featureInstall(self, feature, hostInstanceId, request_id):
        from .app_features import record
        from .app_updates import stage, activate, installed_extras
        request_id = request_id or uuid.uuid4().hex
        try:
            if hostInstanceId != self.service.instance_id:
                raise ValueError('The app host changed. Check desktop setup again before installing.')
            if feature != 'native-desktop':
                raise ValueError('Only native-desktop can be added through this action.')
            state = self.service.state['updates']
            if (self.closed or self.lock.locked() or self.awaiting_restart()
                    or state.get('pendingApp') or state.get('pendingRelease')):
                raise ValueError('Another update is pending. Finish or inspect that update before adding a feature.')
            async with self.lock:
                if feature in installed_extras():
                    await record(self, request_id, 'already_installed', detail='The native observation package is already installed. Check readiness; this request changed nothing.')
                    return
                await record(self, request_id, 'staging', detail='Qualifying native observation against this same app and its existing components.')
                await stage(self, feature=feature, request_id=request_id)
            await activate(self)
        except asyncio.CancelledError:
            await record(self, request_id, 'interrupted', detail='The request was interrupted. Inspect update diagnostics before any retry.')
            raise
        except Exception as error:
            await record(self, request_id, 'error', detail=str(error) if isinstance(error, ValueError) else 'The feature request did not finish. Inspect update diagnostics before retrying; nothing was replayed.')
            raise

    async def app(self):
        if self.lock.locked() or self.awaiting_restart():return
        from .app_updates import stage,activate
        async with self.lock:
            if not self.service.state['updates'].get('pendingApp'):
                await stage(self)
        await activate(self)

    async def install(self):
        if self.awaiting_restart() or self.lock.locked():return
        state = self.service.state['updates']
        if state.get('sequence', {}).get('stage') == 'included' and state['sequence'].get('included', {}).get('issues'):
            await self.publish(detail='Resolve the included component issues and check again before installing.')
            return
        if state.get('sequence'):
            await self.publish(sequence={**state['sequence'], 'install': True})
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
            stage_id=uuid.uuid4().hex
            self.diagnostics.record('ecosystem-stage','started',commandId=stage_id)
            stage=self.directory/'releases'/release
            stage.mkdir(parents=True,mode=0o700)
            source=foundation_home(self.home)
            phase='ecosystem-stage'
            try:
                await self.publish(phase='staging',detail='Preparing an isolated copy of the ecosystem…',error=None)
                if source.exists():
                    await self.diagnostics.run('ecosystem-copy',asyncio.to_thread,shutil.copytree,source,stage/'foundation',symlinks=True)
                else:
                    (stage/'foundation').mkdir()
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
                    if row.get('kind') == 'included source':
                        from .update_sequence import stage_missing
                        await self.diagnostics.run('ecosystem-fetch', stage_missing, self, stage/'foundation', row)
                        continue
                    if row.get('kind') in {'runtime dependency', 'runtime environment'}:
                        continue
                    target=stage/'foundation'/row['path']
                    if not target.resolve().is_relative_to((stage/'foundation').resolve()): raise ValueError('Invalid cache path')
                    phase='ecosystem-source-preflight'
                    current=await self.diagnostics.run(phase,process,'git','rev-parse','HEAD',cwd=target)
                    dirty,artifacts=await self.diagnostics.run(phase,cache_changes,target)
                    if dirty or current!=row['current']: raise ValueError('Source changed since check')
                    meta=target/'.amplifier_cache_meta.json'
                    data=json.loads(meta.read_text())
                    if source_key(data['git_url'], data.get('ref') or 'HEAD') != source_key(row['url'], row['ref']):
                        raise ValueError('Source identity changed since check')
                    if artifacts:
                        # Restore only verified tracked artifacts in this copy.
                        # Rechecking here also protects edits made after check.
                        await self.diagnostics.run(phase,process,'git','--literal-pathspecs','-c','core.hooksPath=/dev/null','restore','--source=HEAD','--worktree','--',*artifacts,cwd=target)
                    await self.publish(detail='Downloading '+row['label']+'…')
                    await self.diagnostics.run('ecosystem-fetch',process,'git','-c','core.hooksPath=/dev/null','fetch','--depth=1',row['url'],row['latest'],cwd=target)
                    await self.diagnostics.run('ecosystem-checkout',process,'git','-c','core.hooksPath=/dev/null','checkout','--detach',row['latest'],cwd=target)
                    data.update(commit=row['latest'],cached_at=time.strftime('%Y-%m-%dT%H:%M:%S'))
                    meta.write_text(json.dumps(data))
                await self.publish(phase='validating',detail='Validating bundles and modules in a separate runtime…')
                phase='ecosystem-validation'
                await self.validate(stage,release)
                write_private(stage/'validated.json',json.dumps({'createdAt':time.time(),'sources':len(candidates),'hostVersion':__import__('amplifier_web').__version__, 'updateTier': self.service.state['updates'].get('sequence', {}).get('stage')}))
                self.diagnostics.clear_failure()
                self.diagnostics.record('ecosystem-stage','succeeded',commandId=stage_id)
                await self.publish(phase='staged',pendingRelease=release,detail='Update validated; waiting for conversations and calls to be idle.')
            except BaseException as error:
                from .update_diagnostics import exception_type
                interrupted=isinstance(error,asyncio.CancelledError)
                status='interrupted' if interrupted else 'failed'
                last=self.diagnostics.state.get('latest',{})
                if last.get('status') not in {'failed','interrupted'}:
                    self.diagnostics.record(phase,status,**{'errorType':exception_type(error),**getattr(error,'diagnostic_facts',{})})
                failure=self.diagnostics.state['lastFailure']
                self.diagnostics.record('ecosystem-stage',status,commandId=stage_id,preserve_last_failure=True)
                await self.publish(phase='interrupted' if interrupted else 'error',pendingRelease=None,
                    error='Ecosystem update '+('interrupted' if interrupted else 'failed')+' during '+failure['phase'].replace('-',' ')+'. Current sources remain active; no conversation work was replayed.')
                if not isinstance(error,Exception):raise
                return
        await self.activate()

    async def validate(self,stage,release):
        from .runtime_environment import stage as stage_runtime, receipt_directory
        from .runtime_qualification import freeze, prepare_overrides, verify_recorded
        receipt=receipt_directory(self.home,release)
        fresh=not (receipt/'runtime.lock').exists()
        project=await stage_runtime(self, release, [row for row in self.inventory if row.get('eligible') and (row.get('status') == 'update' or
            (row.get('kind') == 'runtime dependency' and row.get('status') == 'current'))], finalize=not fresh)
        state=self.service.get_state()
        # Browsing historical CLI projects does not opt their old bundles into
        # this application's update validation or mount missing workspaces.
        configs={(s['workspace'],s['bundle']) for s in state['sessions']
                 if not s.get('historyManaged') and s.get('workspace') and s.get('bundle')}
        configs.add((state['settings']['workspace'],state['settings']['bundle']))
        env={**os.environ,'AMPLIFIER_WEB_HOME':str(stage),'AMPLIFIER_HOME':str(stage/'shared-config'),'AMPLIFIER_UNIFIED_RELEASE':''}
        async def probe(project, *, refresh=False):
            qualified=fresh or (receipt/'runtime-installed.json').exists()
            overrides=await self.diagnostics.run('ecosystem-runtime-policy',prepare_overrides,project,receipt/'runtime-install-overrides.txt') if qualified else Path(__file__).parent/'runtime_deps/compatibility.txt'
            command=[shutil.which('uv'),'run','--locked','--project',str(project),'--python','3.13','python',str(Path(__file__).with_name('update_probe.py'))]
            flags=['--install-overrides',str(overrides)] if qualified else []
            if refresh:flags.append('--refresh-dependencies')
            for workspace,bundle in sorted(configs):
                await self.diagnostics.run('ecosystem-probe',process,*command,workspace,bundle,*flags,
                    env={**env,'UV_OVERRIDE':str(overrides)},timeout=900)
        if fresh:
            # The refresh activator dies with each short-lived probe process.
            # Capture after dynamic module installation, then recreate an
            # ordinary resolver against the frozen graph before activation.
            await probe(project,refresh=True)
            project=await freeze(self,release,project)
        if (project/'.venv').exists():
            verify_recorded(project,receipt)
        await probe(project)
        verify_recorded(project,receipt)

    async def activate(self, rollback=False):
        async with self.service.runtime_lifecycle():
            await self._activate(rollback)

    async def _activate(self, rollback=False):
        if self.lock.locked() or self.awaiting_restart(): return
        async with self.lock:
            if self.closed: return
            pointer=active_release(self.home)
            state=self.service.state['updates']
            retrying_rollback=rollback and 'pendingRollback' in state
            target=state.get('pendingRollback') if retrying_rollback else pointer.get('previous') if rollback else state.get('pendingRelease')
            if target is not None and (not isinstance(target,str) or not re.fullmatch(r'[a-f0-9]{32}',target)):
                raise ValueError('Invalid ecosystem release identity')
            if rollback and not retrying_rollback and 'previous' not in pointer: return
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
                if self.service.closed:
                    return
                if self.busy():
                    self.service.state['updates'].update(detail='Waiting for active work and calls to finish. Try rollback again when idle.' if rollback else 'Update ready; it will activate when work and calls finish.')
                    self.service._publish()
                    return
                if rollback:
                    # Persist the requested target before promotion.  A retry
                    # after committing active.json must not reinterpret the
                    # swapped pointer and roll forward again.
                    self.service.state['updates']['pendingRollback'] = target
                self.service.state['updates'].update(phase='activating',detail='Switching ecosystem version…')
                self.service._publish()
            candidate = None
            try:
                # New work is gated during this short phase. Old sessions remain
                # durable.  The pointer is committed only after replacement
                # construction succeeds, and the live manager is replaced before
                # its terminal close can leave this host unable to admit work.
                candidate = self.service.runtime_candidate()
                # A promotion may have committed before a runtime replacement
                # or state publication failed.  Its durable pointer already
                # retains the rollback identity, so finish it without
                # rewriting previous to the target itself.
                if pointer.get('current') != target:
                    write_private(self.directory/'active.json',json.dumps({'current':target,'previous':pointer.get('current'),'at':time.time()}))
                    if not rollback and 'pendingRollback' in self.service.state['updates']:
                        # The pointer is now the durable rollback authority for
                        # this normal promotion.  Discard an older failed
                        # rollback intent before any later publication can fail.
                        async with self.service.lock:
                            self.service.state['updates'].pop('pendingRollback', None)
                            self.service._publish()
                await self.service.replace_runtime(candidate)
                if rollback:
                    async with self.service.lock:
                        self.service.state['settings']['updates']['autoInstall'] = False
                tier = marker.get('updateTier') if target else None
                items=[row for row in self.service.state['updates'].get('items',[]) if row.get('id')=='application'] if rollback else [{**row, **({'status':'current','current':row['latest']} if row.get('status')=='update' and row.get('id')!='application' and (not tier or row.get('updateTier') == tier) else {})} for row in self.service.state['updates'].get('items',[])]
                sequence = self.service.state['updates'].get('sequence')
                if sequence:
                    from .update_sequence import summary
                    tier_summary = summary([row for row in items if row.get('updateTier') == tier])
                    sequence = {**sequence}
                    if rollback:
                        sequence.update(stage='complete', install=False)
                        sequence.pop('nextStage', None)
                    elif tier == 'included':
                        sequence.update(nextStage='other', included=tier_summary)
                    else:
                        sequence.update(stage='complete', install=False, other=tier_summary)
                await self.publish(phase='installed',release=target,pendingRelease=None,canRollback=True,items=items,error=None,
                    **({'sequence': sequence} if sequence else {}),
                    available=sum(row.get('status')=='update' for row in items),installedAt=time.time(),detail='Previous ecosystem restored. Automatic installation is now off.' if rollback else 'Update installed. Conversations will resume with the new ecosystem.')
                if rollback or 'pendingRollback' in self.service.state['updates']:
                    async with self.service.lock:
                        self.service.state['updates'].pop('pendingRollback', None)
                        self.service._publish()
                self.diagnostics.clear_failure()
                self.diagnostics.record('ecosystem-activation','succeeded')
                self.inventory=[]
                # The release is live and the replacement runtime installed.
                # A stale inventory is recoverable; it must not rewrite this
                # successful activation as an installation failure.
                try:
                    write_private(self.directory/'inventory.json','[]')
                except OSError:
                    pass
            except asyncio.CancelledError:
                await self.service.discard_runtime(candidate)
                raise
            except Exception as error:
                await self.service.discard_runtime(candidate)
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
        sequence = state.get('sequence', {})
        if sequence.get('nextStage') and state.get('phase') not in {'error','interrupted'}:
            await self.check(tier=sequence['nextStage'], install=sequence.get('install', False))
        elif settings.get('autoCheck',True) and time.time()-max(state.get('lastCheck') or 0,state.get('lastAttempt') or 0)>=settings.get('intervalHours',24)*3600:
            await self.check()
        state=self.service.state['updates']
        if (settings.get('autoInstall',False) or state.get('sequence', {}).get('install')) and state.get('phase')=='available' and state.get('available',0):
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
            sequence = self.service.state['updates'].get('sequence', {})
            # Continue promptly, with a bounded wait even if another command
            # holds the update lock. Ordinary checks retain their cadence.
            delay = 1 if sequence.get('nextStage') and self.service.state['updates'].get('phase') == 'installed' else 60
            await asyncio.sleep(delay)

    async def close(self):
        self.closed=True
        if self.readiness_task:
            self.readiness_task.cancel()
            await asyncio.gather(self.readiness_task,return_exceptions=True)
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task,return_exceptions=True)
