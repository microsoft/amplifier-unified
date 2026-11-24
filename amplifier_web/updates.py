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
import subprocess
import sys
import time
import tomllib
from types import SimpleNamespace
from urllib.parse import urlsplit
import uuid
from .host.config import write_private
from . import update_uv_cache

DEFAULT_CHECK_INTERVAL_HOURS = 4
CHECK_INTERVAL_HOURS = (1, 4, 8, 24)
# A host start checks soon after it when the last check was older than this
# at that moment. The timed check above is unchanged; a PC that restarts does
# not wait the rest of an interval for a release that came out meanwhile.
HOST_START_CHECK_AFTER_HOURS = 1


def work_paused(state):
    updates = state.get('updates', {})
    from .app_replacement import pending
    return updates.get('phase') == 'activating' or bool(updates.get('pendingRestart')) or pending(updates)


def new_work_paused(state):
    drain = state.get('updates', {}).get('drain') or {}
    return work_paused(state) or bool(drain.get('safeUpdate') and drain.get('phase') != 'released')


def pending_tool_activations(state):
    """Tool activations that still own a handoff after staging."""
    return [row for row in state.get('pendingSmartTools') or []
            if not row.get('runnerSource') and not row.get('failure')]


def prepared_tool_activations(state):
    """A completed forward stage may wait while the component sequence continues."""
    if state.get('phase') not in {'staged', 'installed', 'checked', 'available'}:
        return False
    rows = pending_tool_activations(state)
    return bool(rows) and all(
        all(isinstance(row.get(key), str) and row[key] for key in ('previous', 'revision', 'target'))
        and row['previous'] != row['target'] for row in rows
    )


def availability_check_blocked(manager, state, tier):
    if (manager.closed or manager.awaiting_restart() or getattr(manager, '_releasing_update_inputs', False)
            or state.get('pendingRelease')
            or (state.get('pendingApp') or {}).get('featureSelection')):
        return True
    if prepared_tool_activations(state):
        return False
    return any(tier != 'application' or not row.get('target')
               for row in pending_tool_activations(state))


def has_new_app_target(manager, state):
    from .app_updates import supersedes_staged
    if any(not row.get('target') for row in pending_tool_activations(state)):
        return False  # An incomplete stage still owns the handoff.
    return bool(state.get('pendingApp') or (state.get('appAvailable')
                and supersedes_staged(state.get('application', {}), manager.running_identity)))


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


def selected_release(home):
    # Workers capture their source generation at spawn; the serving host follows
    # the current pointer. Never change a running worker's resolver underneath it.
    selected = os.environ.get('AMPLIFIER_UNIFIED_RELEASE')
    identity = (selected or None) if selected is not None else active_release(home).get('current')
    if identity is not None and not re.fullmatch(r'[a-f0-9]{32}', identity):
        raise ValueError('Invalid worker source generation')
    return identity


def foundation_home(home):
    home = Path(home)
    identity = selected_release(home)
    return home / 'updates' / 'releases' / identity / 'foundation' if identity else home / 'foundation'



def safe_label(url):
    parsed = urlsplit(url)
    return (parsed.hostname or 'Git source') + '/' + parsed.path.strip('/').removesuffix('.git')


def group_sources(items):
    """One repository/ref/revision/status per display row, with every role kept.

    Installation consumes the private inventory, never these presentation rows.
    Distinct refs, revisions, tiers and protected states must remain distinct.
    """
    groups = {}
    for item in items:
        row = {k: v for k, v in item.items() if k not in {'path', 'url', 'eligible', 'appSelection', 'availabilityFailure'}}
        if row.get('kind') not in {'bundle / module', 'runtime dependency', 'source group'} or not row.get('ref'):
            groups[('id', row['id'])] = row
            continue
        key = tuple(row.get(k) for k in ('label', 'ref', 'current', 'latest', 'status', 'updateTier'))
        members = row.get('members') or [row]
        if key not in groups:
            groups[key] = {**row, 'members': []}
        target = groups[key]
        known = {member['id'] for member in target['members']}
        target['members'].extend(member for member in members if member['id'] not in known)
    result = []
    for key, row in groups.items():
        members = row.get('members', [])
        if len(members) > 1:
            row['id'] = 'source:' + hashlib.sha256(json.dumps(key).encode()).hexdigest()[:20]
            row['kind'] = 'source group'
            row['cacheCopies'] = sum(member.get('cacheCopies', 1) for member in members if member.get('kind') == 'bundle / module')
            row['packageCount'] = len({member['package'] for member in members if member.get('package')})
            row['usage'] = 'configured' if any(member.get('usage') == 'configured' for member in members) else 'unknown'
            row['usageEvidence'] = sorted({evidence for member in members for evidence in member.get('usageEvidence', [])})
            row['members'] = sorted(members, key=lambda member: (member.get('package', ''), member['id']))
        elif members:
            row = {**members[0]}
        result.append(row)
    return result


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
    settings_cache = getattr(service, "settings_cache", None) or SettingsReadCache()
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
        row = {'reason': reason, 'historical': bool((owner and owner.get('historyManaged') and owner['id'] not in selected_ids and owner.get('status') not in {'working', 'running'}) or (not owner and workspace and str(workspace) not in used_workspaces)), **({'appSessionId': owner['id']} if owner else {}), **({'workspace': str(workspace)} if workspace else {}),
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
    selected_ids = set(state.get('selectedSessionIds', [])) | {state.get('selectedSessionId')}
    used_workspaces = {state['settings']['workspace']} | {row.get('workspace') for row in state['sessions']
        if not row.get('historyManaged') or row['id'] in selected_ids or row.get('status') in {'working', 'running'}}
    selections = {(state['settings']['workspace'], state['settings']['bundle'], None)}
    # Native child/legacy history can be viewable without a resumable identity.
    # Its workspace is still inspected below; it is not a broken root selection.
    if not getattr(service, 'app_only', False):
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

    for workspace, selected, session_id in selections:
        try:
            # Missing registrations and saved sessions remain available for
            # history. Their unavailable directories are not configuration errors.
            if not workspace:continue
            if not Path(workspace).expanduser().is_dir():continue
            config = read_config(workspace, home=home, session_id=session_id, settings_cache=settings_cache,
                                 global_only=getattr(service, "app_only", False))
            # Use the same settings authority as session startup. Retained
            # imported registry aliases must not masquerade as configured use.
            registrations = dict(config.registrations)

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
            if getattr(service, 'app_only', False):
                from .bundles import offered_profiles
                for profile in offered_profiles(config):
                    resolve(profile, 'Offered app profile')
                resolve(selected or config.active_bundle, 'Selected app profile')
            else:
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


async def process(*args, cwd=None, env=None, timeout=90, raw=False, _redaction_env=None):
    from .update_command_details import Capture, STDOUT_LIMIT, STDERR_LIMIT, local_excerpt
    from .update_diagnostics import CommandFailure, CommandTimeout, CommandOutput, CommandBytes, command_facts
    started = time.monotonic()
    caller_env = env
    candidate = update_uv_cache.for_command(args, env) if _redaction_env is None else None
    cache = None
    if candidate and candidate.root.is_dir():
        cache = await candidate.location(args, env, cwd, process, timeout)
        if candidate.selected(cache):
            env = update_uv_cache.environment(env, cache)
    redaction_env = dict(_redaction_env or {})
    redaction_env.update(env if env is not None else os.environ)
    if (env if env is not None else os.environ).get('AMPLIFIER_GITHUB_ACCESS') == 'anonymous':
        from .reset import repository_environment
        env = repository_environment(env)
    elif sys.platform == 'win32' and str(args[0]).replace('\\', '/').rsplit('/', 1)[-1].lower() in {'git', 'git.exe'}:
        # Ordinary checkout/fetch must work in the same deep candidate paths as
        # anonymous reads, without changing credential helpers, EOL or includes.
        from .reset import git_filesystem_environment
        env = git_filesystem_environment(env)
    # All commands here use captured pipes, never an interactive console. A
    # pythonw consumer entry must not create a console for its CLI children.
    native = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
    command = args
    if sys.platform == 'darwin' and Path(args[0]).name == 'uv':
        # launchd normally gives GUI applications only 256 open descriptors.
        # Keep the host's own limit and the original argv for diagnostics/retry.
        launcher = Path(__file__).with_name('mac_uv_exec.py')
        command = (sys.executable, '-I', '-S', str(launcher), *args)
    if time.monotonic() - started >= timeout:
        raise CommandTimeout(time.monotonic() - started)
    proc = await asyncio.create_subprocess_exec(*map(str,command), cwd=cwd, env=env,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        start_new_session=os.name != 'nt', **native)
    out, err = Capture(STDOUT_LIMIT), Capture(STDERR_LIMIT)
    readers = asyncio.gather(out.drain(proc.stdout), err.drain(proc.stderr), proc.wait())
    def receipt():
        facts = command_facts(bytes(out.data), bytes(err.data), proc.returncode, time.monotonic()-started)
        facts.update(stdoutBytes=out.total, stderrBytes=err.total,
                     stdoutTruncated=out.truncated, stderrTruncated=err.truncated)
        return facts
    def detail(facts):
        return {**facts, **local_excerpt(err, redaction_env)}
    try:
        await asyncio.wait_for(asyncio.shield(readers), max(0, timeout - (time.monotonic() - started)))
    except BaseException as error:
        def terminate(force=False):
            # The parent may already have exited while descendants hold pipes.
            try:
                if os.name != 'nt':
                    import signal
                    os.killpg(proc.pid, signal.SIGKILL if force else signal.SIGTERM)
                elif proc.returncode is None:
                    proc.kill() if force else proc.terminate()
            except ProcessLookupError:
                pass
        terminate()
        if os.name == 'nt':
            # Windows launch handoff can leave a legitimate successor holding
            # the pipes after our direct child exited. Do not spend a second
            # drain timeout waiting for that successor or claim to contain it.
            if proc.returncode is None:
                try:
                    await asyncio.wait_for(proc.wait(), 5)
                except TimeoutError:
                    terminate(force=True)
                    await proc.wait()
        else:
            try:
                await asyncio.wait_for(asyncio.shield(readers), 5)
            except TimeoutError:
                terminate(force=True)
                try:
                    await asyncio.wait_for(asyncio.shield(readers), 5)
                except TimeoutError:
                    pass  # Bound descendants that escaped the existing process group.
        readers.cancel()
        await asyncio.gather(readers, return_exceptions=True)
        failure = CommandTimeout(time.monotonic()-started) if isinstance(error, TimeoutError) else error
        failure.diagnostic_facts = {**getattr(failure, 'diagnostic_facts', {}), **receipt()}
        if isinstance(error, TimeoutError):
            failure.diagnostic_facts.update(errorType='TimeoutError', timedOut=True)
        failure.diagnostic_facts['commandAttempts'] = [dict(failure.diagnostic_facts)]
        failure.local_command_details = [detail(failure.diagnostic_facts['commandAttempts'][0])]
        if failure is not error:
            raise failure from None
        raise
    stdout, stderr = bytes(out.data), bytes(err.data)
    facts = receipt()
    facts['commandAttempts'] = [dict(facts)]
    if out.truncated:
        facts['outputLimitExceeded'] = True
        facts['commandAttempts'][0]['outputLimitExceeded'] = True
    if proc.returncode:
        from .reset import anonymous_retry_environment
        evidence = ((b'github.com ' if out.github or err.github else b'') +
                    (b'Authentication failed' if out.auth_failure or err.auth_failure else b'') +
                    (b'\nAMPLIFIER_UPDATE_PROBE={"ok":false,"repositoryAccessFailure":true}\n'
                     if out.access_probe or err.access_probe else b''))
        remaining = timeout - (time.monotonic() - started)
        # Both recovery paths share the same deadline and two-attempt receipt.
        retry = None
        retry_fact = 'anonymousRetry'
        if _redaction_env is None and remaining > 0:
            if (candidate and len(args) > 1 and args[1] in {'lock', 'sync'}
                    and err.missing_git_blob and err.failed_git_reset):
                cache = cache or await candidate.location(args, caller_env, cwd, process, remaining)
                remaining = timeout - (time.monotonic() - started)
                if cache is not None and remaining > 0:
                    try:
                        await candidate.prepare_retry(cache, args, env, cwd, process, remaining, redaction_env)
                    except (OSError, RuntimeError, TimeoutError, ValueError):
                        pass  # Cleanup has its own receipt. Preserve the original checkout failure.
                    else:
                        remaining = timeout - (time.monotonic() - started)
                        if remaining > 0:
                            retry = update_uv_cache.environment(env, cache)
                            retry_fact = 'gitCacheRetry'
            else:
                retry = anonymous_retry_environment(args, env, stdout, stderr + evidence)
        if retry is not None:
            def combine(value):
                later = value.diagnostic_facts
                local_later = getattr(value, 'local_command_details', None) or [
                    {**{key: val for key, val in later.items() if key != 'commandAttempts'}, 'stderr': ''}]
                later[retry_fact] = True
                later['commandAttempts'] = facts['commandAttempts'] + later.get('commandAttempts', [
                    {key: val for key, val in later.items() if key != 'commandAttempts'}])
                value.local_command_details = [detail(facts['commandAttempts'][0])] + local_later
            try:
                result = await process(*args, cwd=cwd, env=retry, timeout=remaining, raw=raw,
                                       _redaction_env=redaction_env)
            except BaseException as failure:
                # Cancellation/spawn failure of the retry must not erase the
                # first failure, and must never itself trigger another retry.
                if not hasattr(failure, 'diagnostic_facts'):
                    from .update_diagnostics import exception_facts
                    failure.diagnostic_facts = exception_facts(failure)
                combine(failure)
                raise
            if retry_fact == 'gitCacheRetry':
                candidate.recovered.add(cache)
            combine(result)
            return result
        failure = CommandFailure(facts)
        failure.local_command_details = [detail(facts['commandAttempts'][0])]
        raise failure
    if out.truncated:
        # Successful JSON/binary consumers must never receive a partial result.
        failure = CommandFailure(facts)
        failure.local_command_details = [detail(facts['commandAttempts'][0])]
        raise failure
    result = CommandBytes(stdout, facts) if raw else CommandOutput(stdout.decode(errors='replace').strip(), facts)
    # No success output is retained locally; a retry still carries the first failure.
    return result


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


async def cache_checkout_options(root):
    """Read the validated local EOL scalar, never personal transport policy."""
    from .runtime_qualification import _installed_git_environment, _installed_checkout_environment
    isolated = _installed_git_environment()
    checkout = await asyncio.to_thread(_installed_checkout_environment, root, isolated)
    if checkout is not isolated:
        # Reuse installed provenance's validated scalar, not personal auth,
        # includes or rewrites. process() re-sanitizes anonymous environments,
        # so carry only this local-checkout value as an explicit Git argument.
        last = int(checkout['GIT_CONFIG_COUNT']) - 1
        return ['-c', 'core.autocrlf=' + checkout[f'GIT_CONFIG_VALUE_{last}']]
    return []


async def cache_changes(root):
    """Return protected changes and proven cache artifacts, without editing files.

    Imported caches used to flatten symlinks, and some upstreams track generated
    bytecode. Wiki-weaver's reviewed build hook also bakes a version into source.
    Only unstaged, exactly verified generated forms may be restored later,
    inside an isolated staging copy. Untracked files are never removed.
    """
    root = Path(root).resolve()
    options = await cache_checkout_options(root)
    records = (await process('git',*options,'--no-optional-locks','status','--porcelain=v1','-z','--untracked-files=no',cwd=root,timeout=10,raw=True)).split(b'\0')
    changed = None
    if options and any(record.startswith(b' M ') for record in records):
        # With optional index writes disabled, status may keep a stat-only M
        # after changing EOL interpretation. Ask Git for actual worktree edits.
        # Staged/type/deleted changes below remain protected independently.
        changed = set((await process('git',*options,'--no-optional-locks','diff',
            '--no-ext-diff','--no-textconv','--name-only','-z','--',
            cwd=root,timeout=10,raw=True)).split(b'\0'))
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
        if change == ' M' and changed is not None and os.fsencode(path) not in changed:
            continue
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
        if os.name == 'nt':
            # Worker generation leases share this parent. A mode-only mkdir
            # does not bind its owner to TokenUser; create privately BEFORE
            # any update or lease payload, and refuse unsafe existing trees.
            from .windows_files import directory
            with directory(self.directory, owned_root=self.home, create=True):
                pass
        else:
            self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = asyncio.Lock()
        self.inventory = []
        from .update_checks import CheckCache
        self.check_cache = CheckCache(self.directory, owned_root=self.home)
        self.check_task = None
        from .shared_settings import SettingsReadCache
        self.settings_cache = SettingsReadCache(limit=512)
        self.task = None
        self.readiness_task = None
        self.closed = False
        self.cleanup_task = None
        self.wakeup = asyncio.Event()
        from .update_readiness import running_identity,valid_target,capture_artifact
        self.running_identity = running_identity()
        state = service.state.setdefault('updates', {})
        self._running_artifact = capture_artifact(self)
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
        # A read-only manual verifier interrupted by host exit is not replayed.
        for result in state.get('manualHandoffResults', {}).values():
            if result.get('status') == 'queued':
                result.update(status='interrupted', detail='Manual verification was interrupted; no handoff success was inferred.')
        state['applicationCheckPending'] = False
        from .app_features import reconcile_requests
        reconcile_requests(state)
        state.setdefault('items', [])
        from .app_updates import version_tuple,application_state
        running_application=application_state()
        application={**running_application,**state.get('application',{}),'current':__import__('amplifier_web').__version__,'canInstall':running_application.get('canInstall',True)}
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
        from .update_health import activation, publish as publish_health
        self._active_update_evidence = {}
        self._update_health_initial = True
        self._host_started = time.time()  # See check_due.
        self._start_check_open = True
        self._activation_failed = None  # See _activation_held.
        self._update_health_seen_revision = service.state.get('configurationRevision')
        from .update_restart import target
        state['activation'] = activation(self)
        state['forceRestartTarget'] = target(self)
        publish_health(self)
        from .windows_launcher_follow import project
        project(self)
        if restart_repair:
            self.diagnostics.record('restart-repair','failed',errorType='ValueError',preserve_last_failure=True)
        else:
            service._save_changes(globals={'updates'})

    async def confirm_readiness(self, health, expected=None):
        from .update_readiness import confirm_readiness
        return await confirm_readiness(self, health, expected=expected)

    def awaiting_restart(self):
        from .windows_launcher_follow import fenced
        if fenced(self): return True
        from .update_readiness import recovery_candidate
        # A legacy erased-marker receipt must not permanently prevent repairs
        # after an unrelated manual upgrade. Only its active health check gates
        # another update; an actual pending handoff remains gated until verified.
        from .app_replacement import pending
        return bool(pending(self.service.state['updates']) or self.service.state['updates'].get('pendingRestart') or
                    (self.readiness_task and not self.readiness_task.done() and recovery_candidate(self)))

    async def publish(self, **values):
        async with self.service.lock:
            if values.get('error'):
                group = self.diagnostics.state.get('activeFailures', {}).get(
                    getattr(self.diagnostics, '_pending_failure', None))
                if group:
                    group['displayError'] = values['error']
                self.diagnostics._pending_failure = None
            self.service.state['updates'].update(values)
            self._derive_state()
            self.service._publish_changes(globals={'updates','settings'})

    def _derive_state(self):
        """Refresh the publication fields under service.lock; report any change."""
        state = self.service.state['updates']
        fields = ('blockers', 'activation', 'detail', 'currentStatus', 'forceRestartTarget')
        before = tuple(state.get(key) for key in fields)
        from .update_health import activation, publish as publish_health, PREPARED_STARTING, PREPARED_WAITING
        state['blockers'] = (self.app_blockers() if state.get('pendingApp')
                             else self.blockers()) if self.requires_idle(state) else []
        state['activation'] = activation(self)
        if (state['activation'] in {'starting', 'waiting'}
                and state.get('detail') in (PREPARED_STARTING, PREPARED_WAITING)):
            state['detail'] = PREPARED_STARTING if state['activation'] == 'starting' else PREPARED_WAITING
        publish_health(self)
        # After the health text, at every place that derives it: the launcher's
        # text replaces or extends it (no IO; it never raises).
        from .windows_launcher_follow import project
        project(self)
        from .update_restart import target
        # Also the identity that task.waitForUpdate names. The page's offer to
        # interrupt work is a matter of its view (update_health.browser_view).
        state['forceRestartTarget'] = target(self)
        return before != tuple(state.get(key) for key in fields)

    def configuration_changed(self):
        """Invalidate the current projection on a source-settings publication."""
        revision = self.service.state.get('configurationRevision')
        if getattr(self, '_update_health_seen_revision', revision) == revision:
            self._update_health_seen_revision = revision
            return False
        self._update_health_seen_revision = revision
        self._update_health_initial = True
        from .update_health import publish as publish_health
        publish_health(self)
        from .windows_launcher_follow import project
        project(self)
        self.wakeup.set()
        return True

    def notify_idle(self, *, settled_session_id=None):
        state = self.service.state['updates']
        if self.closed or state.get('phase') not in {'staged', 'app-staged'} or not self.requires_idle(state):
            return
        drain = state.get('drain') or {}
        boundary = drain.get('workers', {}).get(settled_session_id)
        if drain.get('phase') in {'waiting', 'draining'} and boundary:
            # A committed native park/retirement event can invalidate the saved
            # unready observation. Retain it even during an activation attempt;
            # the next owner tick must still obtain positive boundary proof.
            self.wakeup.set()
        elif not self.lock.locked() and not (self.app_busy() if state.get('pendingApp') else self.busy()):
            # Ordinary publications retain the idle-only signal. In particular,
            # the updater's own observations never poll an unready boundary.
            self.wakeup.set()

    @staticmethod
    def requires_idle(state):
        return bool(state.get('pendingApp') or state.get('pendingRelease') or any(
            not item.get('runnerSource') and not item.get('failure') for item in state.get('pendingSmartTools', [])))

    def blockers(self):
        from .update_blockers import blockers
        return blockers(self.service)

    def busy(self):
        return bool(self.blockers())

    def app_busy(self):
        return bool(self.app_blockers())

    def app_blockers(self):
        return self.blockers()

    async def inventory_sources(self, *, indexed=False):
        """Checks use the app index; an explicit audit also inspects saved scopes."""
        if not indexed:
            return await self._scan_inventory()
        from .update_inventory_index import inventory
        return await inventory(self, lambda: self._scan_inventory(app_only=True))

    async def _scan_inventory(self, *, app_only=False):
        from .update_sequence import app_selection
        selection = await asyncio.to_thread(app_selection, self.home)
        base = foundation_home(self.home)
        # Capture only selection metadata before yielding. The history catalog
        # can change while filesystem/settings reads run outside the event loop.
        state = self.service.state
        snapshot = SimpleNamespace(data_dir=self.home, state={
            'selectedSessionId': state.get('selectedSessionId'),
            'selectedSessionIds': [client.get('selectedSessionId') for client in getattr(self.service.clients, 'records', {}).values()],
            'settings': {key: state['settings'][key] for key in ('workspace', 'bundle')},
            'sessions': [{key: row[key] for key in (
                'id', 'workspace', 'bundle', 'runtimeSessionId', 'nativeIdentity',
                'historyManaged', 'historyReadOnlyReason', 'status') if key in row}
                for row in state['sessions']],
            'workspaces': [{key: row[key] for key in ('nativeProject', 'path', 'available') if key in row}
                           if isinstance(row, Mapping) else row
                           for row in state.get('workspaces', [])],
        })
        snapshot.app_only = app_only
        snapshot.source_issues = []
        snapshot.settings_cache = self.settings_cache
        configured, incomplete = await asyncio.to_thread(configured_sources, snapshot)
        rows = []
        semaphore = asyncio.Semaphore(8)
        async def inspect(meta):
            async with semaphore:
                return await inspect_one(meta)
        async def inspect_one(meta):
            # Each cache may include nested skills copies. All are app-owned;
            # skip symlinked external worktrees and malformed metadata.
            root = meta.parent
            if root.is_symlink() or not root.resolve().is_relative_to(base.resolve()): return None
            try:
                data = json.loads(meta.read_text())
                url, ref = data['git_url'], data.get('ref') or 'HEAD'
                parsed = urlsplit(url)
                if parsed.scheme not in {'https','http','ssh'} or not parsed.hostname: return None
                relative = str(root.relative_to(base))
                identity = hashlib.sha256(relative.encode()).hexdigest()[:20]
                current = await process('git','rev-parse','HEAD',cwd=root,timeout=10)
                evidence = sorted(configured.get(source_key(url, ref), ()))
                dirty, _ = await cache_changes(root)
                return {'id':identity,'label':safe_label(url),'ref':ref,'current':current,
                    'status':'local_changes' if dirty else 'pinned' if pinned(ref) else 'not_checked',
                    'usage':'configured' if evidence else 'unknown','usageEvidence':evidence,
                    **({'detail':'Tracked source changes are preserved and block automatic updates. Builds can also modify tracked files (including version stamps); without verified provenance these changes are not discarded.'} if dirty else {}),
                    'path':relative,'url':url,'kind':'bundle / module','eligible':not dirty and not pinned(ref)}
            except (OSError, ValueError, KeyError, TypeError, RuntimeError, TimeoutError):
                return {'id':hashlib.sha256(str(root).encode()).hexdigest()[:20],
                    'label':'Unrecognized cached source','status':'check_failed','eligible':False,'kind':'source',
                    'usage':'unknown','usageEvidence':[]}
        metadata = await asyncio.to_thread(lambda: sorted((base/'cache').rglob('.amplifier_cache_meta.json')))
        rows = [row for row in await asyncio.gather(*(inspect(meta) for meta in metadata)) if row]
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
        from .update_sources import bindings
        rows.extend(await bindings(self.home, base, configured))
        from .runtime_environment import update_inventory
        rows.extend(await update_inventory(self.home))
        from .update_sequence import classify
        smart = getattr(self.service, 'smart_tools', None)
        if smart: rows.extend(smart.update_sources())
        if (await asyncio.to_thread(app_selection, self.home)) != selection:
            raise ValueError('App behavior selection changed during inventory')
        return classify(self.home, rows, selection=selection)

    async def command(self, action, args=None, command_id=None):
        if getattr(self, '_manual_handoff_commit_unknown', False):
            return  # Saved commit is unreadable; reload observes it, never replay.
        if action == 'reconcileManualHandoff':
            from .manual_app_handoff import reconcile
            return await reconcile(self, args, command_id)
        if self.awaiting_restart() and action not in {'featureInstall', 'forceRestart'}:return
        previous_error=self.service.state['updates'].get('error')
        previous_event=self.diagnostics.state.get('latest',{}).get('id')
        try:
            if action == 'forceRestart':
                from .app_updates import activate
                await activate(self, force=args)
            elif action == 'featureInstall':
                await self.featureInstall(args['feature'], args['hostInstanceId'], command_id)
            elif action in {'smartToolRollback', 'smartToolRetry', 'smartToolSkip'}:
                await getattr(self, action)(args['id'])
            elif action == 'app':
                if self.check_task and not self.check_task.done():
                    await asyncio.shield(self.check_task)
                state = self.service.state['updates']
                if pending_tool_activations(state) and not has_new_app_target(self, state):
                    await self.check(fresh=True)
                async with self.lock:
                    if self.closed or self.awaiting_restart():return
                    state = self.service.state['updates']
                    sequence = {**state.get('sequence', {}),
                                'stage': 'application', 'install': False}
                    sequence.pop('nextStage', None)
                    intent = {'id': command_id or uuid.uuid4().hex, 'scope': 'app'}
                    pending = self.service.state['updates'].get('pendingApp')
                    await self.publish(applicationIntent=intent, sequence=sequence,
                        **({'pendingApp': {**pending, 'updateIntent': intent}} if pending else {}))
                    if pending_tool_activations(state) and not has_new_app_target(self, state):
                        return
                await self.app()
            elif action == 'install':
                from .windows_launcher_follow import request_by_button
                if await request_by_button(self): return
                # A click during a check must not disappear at install()'s
                # busy guard. Join that check, then re-evaluate its inventory.
                if self.check_task and not self.check_task.done():
                    await asyncio.shield(self.check_task)
                    if self.service.state['updates'].get('phase') in {'error', 'interrupted'}:
                        return
                def found():
                    # After a new check the request installs what that check found.
                    state = self.service.state['updates']
                    return state.get('phase') == 'available' and state.get('available', 0)
                async with self.lock:
                    if self.closed or self.awaiting_restart():return
                    retry = self._failed_component_retry()
                if retry:
                    # _check takes this same lock. Refresh outside it, before
                    # publishing intent (_check clears applicationIntent).
                    await self.check(fresh=True)
                async with self.lock:
                    if self.closed or self.awaiting_restart():return
                    intent = {'id': command_id or uuid.uuid4().hex, 'scope': 'whole'}
                    pending = self.service.state['updates'].get('pendingApp')
                    await self.publish(applicationIntent=intent,
                        **({} if retry else {'retryCheck': None}),
                        **({'pendingApp': {**pending, 'updateIntent': intent}} if pending else {}))
                    if retry and not found():
                        failed = self.service.state['updates'].get('phase') in {'error', 'interrupted'}
                        await self.publish(retryCheck={'at': time.time(), 'attemptId': None,
                            'result': 'check-failed' if failed else 'nothing-to-install'})
                        return
                    prior_attempt = self.diagnostics.state.get('batch', {}).get('attemptId')
                if not self.closed and not self.awaiting_restart():
                    await self.install_checked(found)
                    if retry:
                        async with self.lock:
                            # Settings-race checks clear intent; a newer request
                            # replaces it and must keep its own result.
                            if self.service.state['updates'].get('applicationIntent') not in (None, intent):return
                            batch = self.diagnostics.state.get('batch', {})
                            attempt = batch.get('attemptId') if (batch.get('kind') == 'ecosystem'
                                and batch.get('attemptId') != prior_attempt) else None
                            # 'retried' only names a component attempt that this
                            # request started. 'other-update': it started an update
                            # of another kind (the application comes first).
                            started = batch.get('attemptId') not in (None, prior_attempt)
                            await self.publish(retryCheck={'at': time.time(), 'attemptId': attempt,
                                'result': 'retried' if attempt else 'other-update' if started else 'not-started'})
            else:
                await getattr(self, action)()
        except asyncio.CancelledError: raise
        except Exception as error:
            from .update_restart import RestartRejected
            if action == 'forceRestart' and isinstance(error, RestartRejected):
                await self.publish(forceRestartResult={'requestId': command_id, 'status': 'rejected', 'detail': str(error)})
                return
            # Feature requests own a durable per-request result. A rejected
            # concurrent request must not change an active update's work gate.
            if action == 'featureInstall':return
            if self.service.state['updates'].get('phase')=='error' and self.service.state['updates'].get('error') and self.service.state['updates']['error']!=previous_error:return
            from .update_diagnostics import exception_facts, failure_guidance
            last=self.diagnostics.state.get('latest',{})
            phase=last.get('phase',action)
            if last.get('status')!='failed' or last.get('id')==previous_event:
                self.diagnostics.record(phase,'failed',**exception_facts(error))
            await self.publish(phase='activating' if self.service.state['updates'].get('pendingRestart') else 'error',error=failure_guidance(self.diagnostics.state.get('lastFailure', {})) or 'Update failed during '+phase.replace('-',' ')+'. Review the diagnostic receipt; no conversation work was replayed.')
        finally:
            # Manual actions used to leave ready work behind a 60-second
            # polling sleep. Wake the owner; keep all admission/idle checks in
            # tick(), rather than installing in parallel with another update.
            self.wakeup.set()

    def _failed_component_retry(self):
        """Called under the manager lock; interrupted/pending owners keep their paths."""
        state = self.service.state['updates']
        diagnostics = self.diagnostics.state
        batch = diagnostics.get('componentPreparation', diagnostics.get('batch', {}))
        failure = batch.get('failure') or diagnostics.get('lastFailure') or {}
        return (state.get('phase') == 'error' and batch.get('kind') == 'ecosystem'
            and bool(batch.get('attemptId')) and failure.get('attemptId') == batch['attemptId']
            and failure.get('kind') == 'ecosystem' and failure.get('status') == 'failed'
            and (batch.get('retry') or {}).get('mode') == 'changed-inputs'
            and not any(state.get(key) for key in ('pendingApp', 'pendingRelease', 'pendingRollback',
                                                  'pendingRestart', 'pendingReplacement', 'pendingSmartTools')))

    @staticmethod
    def independent_tool_results(state):
        return bool(state.get('pendingSmartTools')) and not pending_tool_activations(state)

    async def check(self, *, tier='application', install=False, fresh=True, automatic=False):
        if getattr(self, '_manual_handoff_commit_unknown', False):return
        state = self.service.state['updates']
        if availability_check_blocked(self, state, tier):
            return  # A timing measurement must not change a pending handoff.
        if self.check_task and not self.check_task.done():
            # Join only an equivalent availability operation. A manual refresh
            # must not inherit a background TTL result, tier or install action.
            previous = self.check_task
            equivalent = getattr(self, 'check_request', None) == (tier, install, fresh, automatic)
            result = await asyncio.shield(previous)
            if equivalent:
                return result
            return await self.check(tier=tier, install=install, fresh=fresh, automatic=automatic)
        async def run():
            from .update_checks import checking
            ttl = self.service.state['settings']['updates'].get('intervalHours', DEFAULT_CHECK_INTERVAL_HOURS) * 3600
            started = time.monotonic()
            started_at = time.time()
            async with checking(self.check_cache, fresh=fresh, ttl=ttl):
                await self._check(tier=tier, install=install, fresh=fresh, automatic=automatic)
            timing = {'startedAt': started_at, 'finishedAt': time.time(),
                      'elapsedMs': round((time.monotonic()-started)*1000), **self.check_cache.stats}
            await self.publish(checkTiming=timing,
                               recentChecks=[*self.service.state['updates'].get('recentChecks', [])[-4:], timing])
        self.check_request = (tier, install, fresh, automatic)
        self.check_task = asyncio.create_task(run())
        return await asyncio.shield(self.check_task)

    async def _check(self, *, tier='application', install=False, fresh=True, automatic=False):
        from .update_sequence import summary
        from .update_health import pending_preparation, preparation_owner_stopped, reconcile_preparation
        async with self.lock:
            state = self.service.state['updates']
            # A capacity observation can own this lock when a refresh arrives.
            # Queue that refresh, then recheck admission against current state.
            if availability_check_blocked(self, state, tier):
                return
            from .update_drain import verified_target
            drain = state.get('drain') or {}
            if drain.get('safeUpdate') and drain.get('phase') == 'replacing' and verified_target(self, drain):
                tier, install = 'included', True
                # A fresh check invalidates the previous proof before any await.
                drain['componentsReady'] = False
            # A check has passed its admission since this host started. That
            # ends the start reason of check_due; a refusal above does not.
            self._start_check_open = False
            if self.independent_tool_results(state):
                # Retained runner qualification and failed optional tools own
                # their rows independently of the new sequence/install intent.
                await self.publish(runnerAvailabilityCheck=True)
            if state.get('pendingApp') or (pending_tool_activations(state) and (
                    not prepared_tool_activations(state) or has_new_app_target(self, state))):
                # Refresh only the app row while a tool awaits activation;
                # the staged tool retains its own handoff.
                from .app_updates import check_staged
                await check_staged(self)
                return
            from .manual_app_handoff import continuing, verify_continuation
            manual = continuing(self)
            if manual and tier == 'application' and automatic:
                tier = state['sequence']['stage']
                install = state['sequence'].get('install') is True
            if manual and tier != 'application':
                await verify_continuation(self)
            else:
                manual = False  # A fresh explicit check starts the usual app-first cycle.
            sequence = ({'stage': 'application', 'install': install,
                         'included': summary([], checked=False), 'other': summary([], checked=False)}
                        if tier == 'application' else {**state.get('sequence', {}), 'stage': tier, 'install': install})
            sequence.pop('nextStage', None)
            await self.publish(phase='checking', sequence=sequence, applicationIntent=None,
                               lastAttempt=time.time(),
                               detail='Checking the application release…' if tier == 'application' else 'Checking included components…' if tier == 'included' else 'Checking other components…', error=None)
            try:
                application = state.get('application', {})
                if tier == 'included':
                    from .safe_update import check_host
                    if not await check_host(self):
                        return
                if tier == 'application':
                    from .app_updates import check as check_application
                    application = await check_application()
                    previous=self.service.state['updates'].get('application',{})
                    if application.get('status')=='check_failed':
                        from .release_notes import history
                        from .app_updates import version_tuple
                        application['releaseNotes']=history(previous.get('releaseNotes',[]),previous.get('latest') if version_tuple(previous.get('latest')) else None)
                        # A failed component stage follows a verified, equal release.
                        application['releaseNotesWarning']=('One component of the installed app could not be checked. The newest app release is installed; its saved release notes are shown.'
                            if application.get('checkStage')=='components' else
                            'The update check failed. Showing saved release notes; check again to confirm the latest release.')
                    elif application.get('releaseNotesWarning') and application.get('revision')==previous.get('revision'):
                        from .release_notes import history
                        application['releaseNotes']=history(previous.get('releaseNotes',[]),application.get('latest'))
                        application['releaseNotesWarning']='Release notes could not be refreshed. Showing saved release history.'
                    application['runningRevision'] = self.running_identity['revision']
                    app_available = application.get('status') == 'update'
                    # A selected app replacement still owns the next activation.
                    # An unavailable release check does not select a replacement:
                    # independently check the running app's component declarations.
                    self.inventory = []
                    write_private(self.directory/'inventory.json', '[]')
                    await self.publish(application=application, appAvailable=app_available,
                                       items=[application], available=int(app_available))
                    if app_available or application.get('status') not in {'current', 'check_failed', 'release_channel_needed'}:
                        await self.publish(phase='available' if app_available else 'error', lastCheck=time.time(),
                            error=None if app_available else application.get('detail') or 'The application check returned an unrecognized outcome. Check again before continuing.',
                            detail='App update available. Included components will be checked after the new app restarts.' if app_available else 'Component checks are waiting for the application check.')
                        return
                    tier = 'included'
                pending = pending_preparation(self.diagnostics.state)
                if pending and preparation_owner_stopped(self, pending.get('preparation') or {}):
                    reconcile_preparation(self)
                if pending_preparation(self.diagnostics.state):
                    await self.publish(phase='checked', application=application,
                        detail='Component checks are paused until the interrupted preparation is reconciled.')
                    return
                started = time.monotonic()
                rows = await self.inventory_sources(indexed=True)
                # Prepared tools and retained attempts already own these selections.
                # Check the remaining sources without restaging the same installation.
                from .managed_runner_backend import skip_key
                retained = {skip_key(row['runnerSource']) for row in state.get('pendingSmartTools', [])
                            if row.get('runnerSource')}
                owned_tools = {row['previous'] for row in state.get('pendingSmartTools', [])
                               if not row.get('runnerSource') and (row.get('failure') or row.get('target'))}
                rows = [row for row in rows if not (row.get('runnerKind') and skip_key(row) in retained)
                        and not (row.get('kind') == 'smart tool' and row.get('installationId') in owned_tools)]
                await self.publish(inventoryTimingMs=round((time.monotonic()-started)*1000), inventoryEvidence=getattr(self, 'inventory_evidence', None))
                remote_tasks = {}
                from .update_checks import git_revision, ref_policy
                async def remote(url, ref, prove_kind):
                    return await ref_policy(url, ref, process) if prove_kind else await git_revision(url, ref, process)
                async def check_row(row):
                    from .managed_runner_backend import backend, skip_key
                    if row.get('runnerKind'):
                        inspector = getattr(backend(row['runnerKind']), 'check_source', None)
                        if inspector is not None:
                            try:
                                await inspector(self.service, row)
                            except (ValueError, RuntimeError, TimeoutError):
                                row.update(status='check_failed', eligible=False)
                                return
                    if not row.get('eligible') or row.get('kind') == 'runtime environment': return
                    if row.get('registryManaged'):
                        from .runtime_registry_updates import check as check_registry
                        try:
                            await check_registry(row)
                        except (ValueError, RuntimeError, TimeoutError) as error:
                            # Still reported and never offered. Add only a closed
                            # reason and plain words; the status is unchanged.
                            row['status'] = 'check_failed'
                            from .update_checks import RegistryLookupFailure, lookup_guidance
                            if isinstance(error, RegistryLookupFailure):
                                row.update(checkReason=error.reason, detail=lookup_guidance(error.reason))
                        return
                    origin = row.get('policyOrigin') or {}
                    typed_policy = (origin.get('kind') in {'prepared-manifest', 'selected-declaration',
                                                          'verified-cache-branch', 'checked-source'}
                                    and all(origin.get(key, '') == row.get(key, '')
                                            for key in ('url', 'ref', 'subdirectory'))
                                    and origin.get('revision') == row.get('current'))
                    # PEP 610 requested_revision has no ref kind. Legacy/raw
                    # installed Git rows need the same proof as cache URIs;
                    # retain an exact already-qualified authored policy.
                    prove_kind = bool(row.get('cacheManaged') or row.get('sharedSource')
                        or row.get('kind') in {'bundle / module', 'included source'}
                        or row.get('kind') == 'runtime dependency' and not typed_policy)
                    key = (row['url'], row['ref'], prove_kind)
                    task = remote_tasks.setdefault(key, None)
                    if task is None:
                        task = remote_tasks[key] = asyncio.create_task(remote(*key))
                    try:
                        result = await task
                        if prove_kind:
                            if result['kind'] == 'unresolved':
                                row.update(status='check_failed', detail='This source reference could not be verified. Its installed version is preserved.')
                                row.pop('latest', None)
                                return
                            if result['kind'] != 'branch':
                                row.update(status='pinned', eligible=False,
                                    detail='This source reference is not an unambiguous branch. Its installed version is preserved.')
                                row.pop('latest', None)
                                return
                            result = result['revision']
                        row['latest'] = result
                        row['status'] = 'update' if row['latest'] != row.get('current') else 'current'
                        if (row.get('kind') == 'smart tool' and row['status'] == 'update'
                                and state.get('skippedSmartTools', {}).get(skip_key(row)) == row['latest']):
                            row.update(status='skipped', detail='This Smart Tool revision was skipped. A newer revision can still be offered.')
                    except (ValueError, RuntimeError, TimeoutError) as error:
                        row['status'] = 'check_failed'
                        from .update_diagnostics import CommandFailure
                        facts = getattr(error, 'diagnostic_facts', {})
                        row['availabilityFailure'] = bool(isinstance(error, CommandFailure)
                            and type(facts.get('exitCode')) is int and facts['exitCode'] > 0
                            and not facts.get('timedOut'))
                checked = []
                safe_included = tier == 'included' and drain.get('safeUpdate') and drain.get('phase') == 'replacing'
                stages = ['included'] if safe_included else ['included', 'other'] if tier == 'included' else ['other']
                for stage in stages:
                    selected = [row for row in rows if row.get('updateTier', 'other') == stage]
                    sequence['stage'] = stage
                    await self.publish(sequence={**sequence}, detail='Checking included components…' if stage == 'included' else 'Checking other components…')
                    await asyncio.gather(*(check_row(row) for row in selected))
                    from .update_partial import retained_after_check, availability
                    await asyncio.to_thread(retained_after_check, self, selected, retry_retained=False)
                    checked.extend(selected)
                    sequence.update(stage=stage)
                    sequence[stage] = summary(group_sources(selected))
                    protected = any(row.get('status') in {'check_failed', 'local_changes'}
                                    and not availability(row) for row in selected)
                    if sequence[stage]['available'] or (stage == 'included' and protected):break
                from .update_sequence import verify_app_selection
                await asyncio.to_thread(verify_app_selection, self, checked)
                if tier == 'included':
                    from .safe_update import included_result
                    included_result(self, group_sources(checked))
                self.inventory = checked
                write_private(self.directory/'inventory.json',json.dumps(checked))
                public = group_sources(checked)
                available = sum(row.get('status') == 'update' for row in public)
                from .update_partial import availability
                blocked = sequence['stage'] == 'included' and any(
                    row.get('status') in {'check_failed', 'local_changes'} and not availability(row)
                    for row in checked if row.get('updateTier') == 'included')
                failed_check = any(row.get('status') == 'check_failed' for row in checked)
                if safe_included and not available and not drain.get('componentsReady'):
                    # A protected or missing required source can be ineligible for
                    # automatic installation. Keep the failed check visible for retry.
                    blocked = failed_check = True
                if not available and not blocked:
                    if safe_included and drain.get('componentsReady'):
                        sequence['nextStage'] = 'other'
                    else:
                        sequence['stage'] = 'complete'
                        sequence['install'] = False
                from .update_health import refresh as refresh_health
                await refresh_health(self)
                await self.publish(phase='error' if blocked and failed_check else 'checked' if blocked else 'available' if available else 'checked',
                    sequence=sequence, items=public+[application], application=application,
                    appAvailable=state.get('appAvailable', False) if manual else False,
                    includedCanProceed=bool(available and not blocked),
                    available=available + int(manual and state.get('appAvailable', False)), lastCheck=time.time(),
                    error='An included component could not be checked. Review its details before continuing.' if blocked and failed_check else None,
                    detail='Included components will be installed before checking the remaining sources.' if sequence['stage']=='included' and available else 'Component check complete.')
            except Exception:
                await self.publish(phase='error', error='Update check failed. Your installed sources are unchanged.')

    async def featureInstall(self, feature, hostInstanceId, request_id):
        from .app_features import FEATURES, record
        from .app_updates import stage, activate, installed_extras, prepare_browser
        request_id = request_id or uuid.uuid4().hex
        try:
            if hostInstanceId != self.service.instance_id:
                raise ValueError('The app host changed. Check setup again before installing.')
            if not isinstance(feature, str) or feature not in FEATURES:
                raise ValueError('Choose a supported optional feature.')
            state = self.service.state['updates']
            if (self.closed or self.lock.locked() or self.awaiting_restart()
                    or state.get('pendingApp') or state.get('pendingRelease') or state.get('pendingSmartTools')):
                raise ValueError('Another update is pending. Finish or inspect that update before adding a feature.')
            async with self.lock:
                if feature in installed_extras():
                    browser_receipt = None
                    if feature == 'managed-browser':
                        import sys
                        self.diagnostics.begin('application', self.running_identity.get('revision'))
                        await record(self, request_id, 'staging', feature=feature,
                            detail='Preparing the browser binary in this service’s own cache.')
                        browser_receipt = await prepare_browser(self, sys.executable, [feature])
                    await record(self, request_id, 'already_installed', feature=feature,
                        **({'managedBrowser':browser_receipt} if browser_receipt else {}),
                        detail='Browser launch verified using '+browser_receipt['selection']+' executable; refresh browser readiness.' if browser_receipt
                            else 'The optional package is already installed. Decision support also requires current conversation components.')
                    return
                await record(self, request_id, 'staging', feature=feature,
                    detail='Qualifying '+FEATURES[feature]+' against this same app and its existing components.')
                await stage(self, feature=feature, request_id=request_id)
            await activate(self)
        except asyncio.CancelledError:
            await record(self, request_id, 'interrupted', feature=feature, detail='The request was interrupted. Inspect update diagnostics before any retry.')
            raise
        except Exception as error:
            await record(self, request_id, 'error', feature=feature, detail=str(error) if isinstance(error, ValueError) else 'The feature request did not finish. Inspect update diagnostics before retrying; nothing was replayed.')
            raise

    async def app(self):
        if self.lock.locked() or self.awaiting_restart():return
        from .manual_app_handoff import awaiting_verification
        if awaiting_verification(self):return
        from .app_updates import stage,activate,supersedes_staged
        state = self.service.state['updates']
        pending = state.get('pendingApp')
        from .safe_update import waiting, component_restart
        pinned = waiting(state) or component_restart(state.get('drain') or {}, pending or {})
        if (pending and not pinned and not pending.get('featureSelection') and not self.app_busy()
                and time.time() - (state.get('pendingAppCheckAt') or 0) >= 60):
            # The idle window may arrive between scheduled checks. Refresh once
            # before activation; a recent check also avoids duplicate requests.
            await self.check(fresh=True)
        async with self.lock:
            if self.closed or self.awaiting_restart():return
            if awaiting_verification(self):return
            state = self.service.state['updates']
            if pending_tool_activations(state) and not has_new_app_target(self, state):
                return
            pending = state.get('pendingApp')
            if not pending or not pinned and supersedes_staged(state.get('application', {}), pending):
                await stage(self)
        await activate(self)

    async def install_checked(self, admitted, *, automatic=False):
        """Install the checked selection. When the settings changed after that check, check once more.

        Only the refusal before anything is prepared arrives here: for example
        after a first AI connection, for which the update waited. The new
        check's selection is installed under the caller's own admission. A
        second refusal is reported as before.
        """
        from .update_sequence import StaleAppSelection
        install = (lambda: self.install(automatic=True)) if automatic else self.install
        try:
            await install()
        except StaleAppSelection:
            await self.check(fresh=not automatic, automatic=automatic)
            if admitted():
                await install()

    async def install(self, *, automatic=False):
        if getattr(self, '_manual_handoff_commit_unknown', False):return
        if self.awaiting_restart() or self.lock.locked():return
        state = self.service.state['updates']
        from .windows_app_recovery import automatic_retry_held
        if automatic and automatic_retry_held(state):
            return  # Do not loop on an exactly failed Windows app revision.
        from .manual_app_handoff import verify_continuation
        manual = await verify_continuation(self)
        if manual and automatic and state.get('sequence', {}).get('install') is not True:
            return  # Verification without saved Install intent installs nothing.
        from .update_health import pending_preparation, reconcile_preparation
        # Independent app/tool owners may proceed without clearing or replaying
        # component work. Their diagnostics.begin must retain this operation.
        if pending_preparation(self.diagnostics.state):
            independent = state.get('pendingApp') or state.get('appAvailable') or state.get('pendingSmartTools')
            if not independent:
                if automatic:
                    return
                async with self.lock:
                    if not reconcile_preparation(self):
                        raise ValueError('The interrupted update has no proof of inactive candidate-only preparation. Inspect its diagnostics before another installation; its outcome is not replayed.')
        from .update_partial import availability
        if (state.get('sequence', {}).get('stage') == 'included'
                and state['sequence'].get('included', {}).get('issues')
                and (not self.inventory or any(row.get('status') in {'check_failed', 'local_changes'}
                    and not availability(row) for row in self.inventory if row.get('updateTier') == 'included'))):
            await self.publish(detail='Resolve the included component issues and check again before installing.')
            return
        if state.get('sequence') and state['sequence'].get('install') is not True:
            # Saved once: an update that waits (for an AI connection, or for
            # changed inputs) reaches this line again at every minute tick.
            await self.publish(sequence={**state['sequence'], 'install': True})
        if pending_tool_activations(state) and not prepared_tool_activations(state):
            if not manual and not state.get('pendingRelease') and has_new_app_target(self, state):
                await self.app()
            else:
                await self.activateSmartTools()
            return
        if self.independent_tool_results(state) and any(state.get(key) for key in ('pendingApp', 'pendingRelease', 'appAvailable')):
            await self.publish(runnerAvailabilityCheck=True)
        if self.service.state['updates'].get('pendingApp'):
            await self.app()
            return
        if self.service.state['updates'].get('pendingRelease'):
            await self.activate()
            return
        if self.service.state['updates'].get('appAvailable') and not manual:
            await self.app()
            return
        pending = state.get('pendingSmartTools', [])
        if pending and all(item.get('failure') for item in pending) and not state.get('runnerAvailabilityCheck'):
            # Preserve explicit retry of the failed batch, but never inherit
            # that retry into a subsequent application/component sequence.
            for item in pending:
                await self.smartToolRetry(item.get('resultId', item['previous']))
            return
        if self.lock.locked(): return
        if not self.inventory:
            path=self.directory/'inventory.json'
            self.inventory=json.loads(path.read_text()) if path.exists() else []
        if (self.independent_tool_results(state) or prepared_tool_activations(state)) and not any(
                row.get('status') == 'update' and row.get('eligible') for row in self.inventory):
            if any(not item.get('failure') for item in state['pendingSmartTools']):
                await self.activateSmartTools()
            return
        if self.independent_tool_results(state):
            await self.publish(runnerAvailabilityCheck=True)
        if any(row.get('kind')=='smart tool' and row.get('status')=='update' and row.get('eligible') for row in self.inventory) and not any(row.get('kind')!='smart tool' and row.get('status')=='update' and row.get('eligible') for row in self.inventory):
            await self.installSmartTools()
            return
        async with self.lock:
            if manual:
                if not await verify_continuation(self, full=True):
                    raise ValueError('The manual handoff continuation was superseded.')
            if not self.inventory:
                path=self.directory/'inventory.json'
                self.inventory=json.loads(path.read_text()) if path.exists() else []
            if not automatic:
                # Only deliberate Install retries an unchanged retained source.
                # Check never grants this intent; receipt/selection checks still
                # bind the release to the current active generation.
                from .update_partial import retained_after_check
                retained_after_check(self, self.inventory, retry_retained=True)
            candidates=[r for r in self.inventory if r.get('status')=='update' and r.get('eligible') and r.get('kind')!='smart tool']
            if pending_preparation(self.diagnostics.state):
                if automatic:return
                if not reconcile_preparation(self):
                    raise ValueError('The interrupted component preparation must be reconciled before another installation.')
            if not candidates:
                await self.publish(detail='Check for updates before installing. No eligible updates are available.')
                return
            from .update_health import read_connection
            waiting = await asyncio.to_thread(read_connection, self)
            changed = getattr(self, '_update_connection', None) != waiting
            self._update_connection = waiting
            if waiting.get('needed'):
                # The selected profile has no provider, so no candidate could
                # qualify. This is not an attempt: no candidate, no diagnostic
                # batch and no retry record. A minute tick repeats no publication.
                if changed:
                    await self.publish()
                return
            from .update_sequence import verify_app_selection
            await asyncio.to_thread(verify_app_selection, self, self.inventory)
            from .update_health import preparation_key, retry_decision, environment_repaired
            input_key = preparation_key(self, self.inventory)
            batch = self.diagnostics.state.get('componentPreparation', self.diagnostics.state.get('batch', {}))
            decision = retry_decision(batch, input_key)
            if automatic and decision and not (decision == 'changed-inputs' and environment_repaired(self, batch)):
                await self.publish()
                return  # No candidate directory, copy, resolution or installation.
            release=uuid.uuid4().hex
            self.diagnostics.begin('ecosystem',release,tier=state.get('sequence',{}).get('stage'),
                                   components=[row.get('package') or row.get('id') if not row.get('url') else
                                               safe_label(row['url']).rsplit('/',1)[-1] for row in candidates])
            self.diagnostics.state.pop('componentPreparation', None)
            self.diagnostics.state['batch']['inputKey'] = input_key
            stage_id=uuid.uuid4().hex
            self.diagnostics.record('ecosystem-stage','started',commandId=stage_id)
            stage=self.directory/'releases'/release
            stage.mkdir(parents=True,mode=0o700)
            from .update_health import pointer_witness, candidate_copy
            proof = {'candidate': release, 'host': self.service.instance_id, 'hostPid': os.getpid(),
                     'activeBefore': pointer_witness(self),
                     'directoryIdentity': [stage.stat().st_dev, stage.stat().st_ino],
                     'boundary': 'copy-only', 'copySettled': True}
            self.diagnostics.state['batch']['preparation'] = proof
            source=foundation_home(self.home)
            phase='ecosystem-stage'
            partial = None
            try:
                await self.publish(phase='staging',detail='Preparing an isolated copy of the ecosystem…',error=None)
                if source.exists():
                    from .update_storage import copy_snapshot
                    await self.diagnostics.run('ecosystem-copy',candidate_copy,proof,copy_snapshot,source,stage/'foundation')
                else:
                    (stage/'foundation').mkdir()
                # No copy of the app's own config or routing folder: config holds
                # the sign-in secrets and the TLS key, and a stage reads neither
                # folder. Its settings and routing are in shared-config, with the
                # temporary copy of the keys file (remove_stage_keys).
                from .session_files import amplifier_home
                from .update_storage import snapshot_shared_config
                shared_stage=stage/'shared-config'
                await snapshot_shared_config(proof,amplifier_home(),shared_stage)
                # Beyond plain snapshots, preparation can invoke shared source
                # stores, installers and native hooks. Do not infer their effects
                # or settlement from a CancelledError.
                await asyncio.to_thread(verify_app_selection, self, self.inventory)
                proof['boundary'] = 'preparing'
                shared_settings = shared_stage/'settings.yaml'
                if shared_settings.exists():
                    write_private(shared_settings, shared_settings.read_text().replace(str(source),str(stage/'foundation'))
                                  .replace(str(self.home/'foundation'),str(stage/'foundation')))
                registry=stage/'foundation/registry.json'
                if registry.exists(): registry.write_text(registry.read_text().replace(str(source),str(stage/'foundation')))
                await asyncio.to_thread(verify_app_selection, self, self.inventory, stage=stage)
                # Snapshot shared configuration, never CLI registry or caches.
                # Candidate preparation resolves declarations into its own store.
                from .update_partial import stage_sources, read as partial_read, public_results
                partial_plan = await stage_sources(self, stage, candidates)
                partial = partial_read(stage)
                if partial and not any(row['status'] == 'awaiting-adoption' for row in partial['results'].values()):
                    raise ValueError('No independent source update remains to qualify.')
                await asyncio.to_thread(verify_app_selection, self, self.inventory, stage=stage)
                await self.publish(phase='validating',detail='Preparing dependencies in a separate runtime…')
                phase='ecosystem-validation'
                from .update_plan import build, reuse_runtime
                plan = await build(self, stage, candidates)
                write_private(stage/'plan.json', json.dumps(plan))
                await self.publish(plan={key: plan[key] for key in ('mode', 'reason', 'activation')})
                if plan['mode'] == 'data':
                    await self.diagnostics.run('ecosystem-runtime-reuse', reuse_runtime, self, stage, plan)
                else:
                    await self.validate(stage,release)
                await partial_plan.recheck()
                await asyncio.to_thread(verify_app_selection, self, self.inventory, stage=stage)
                write_private(stage/'validated.json',json.dumps({'generationSchema': 1, 'workerProtocol': 1, 'createdAt':time.time(),'sources':len(candidates),'hostVersion':__import__('amplifier_web').__version__, 'updateTier': self.service.state['updates'].get('sequence', {}).get('stage'),
                    'appSelection': next(row.get('appSelection') for row in self.inventory
                                         if row.get('kind') != 'smart tool')}))
                from .update_storage import remove_stage_keys
                remove_stage_keys(self.home, stage)
                self.diagnostics.clear_failure()
                self.diagnostics.record('ecosystem-stage','succeeded',commandId=stage_id)
                await self.publish(phase='staged',pendingRelease=release,
                    componentResults=public_results(partial),
                    detail='Update prepared; making it available to new conversation work.')
            except BaseException as error:
                from .update_diagnostics import exception_facts, failure_guidance
                interrupted=isinstance(error,asyncio.CancelledError)
                if isinstance(error, Exception):
                    from .update_storage import remove_stage_keys
                    remove_stage_keys(self.home, stage)
                if interrupted:proof['interruptedAt'] = time.time()
                status='interrupted' if interrupted else 'failed'
                failure=self.diagnostics.state.get('lastFailure',{})
                if failure.get('attemptId')!=self.diagnostics.state.get('attemptId'):
                    self.diagnostics.record(phase,status,**{**getattr(error,'diagnostic_facts',{}),**exception_facts(error)})
                failure=self.diagnostics.state['lastFailure']
                from .update_health import record_retry
                record_retry(self.diagnostics.state['batch'], input_key, failure)
                if interrupted:
                    # Display may preserve a prior handled fetch failure. Actual
                    # cancellation, not that historical display choice, owns the
                    # unknown-operation fence and always wins over changed inputs.
                    self.diagnostics.state['batch']['retry']['mode'] = 'inspect'
                elif partial:
                    # A mixed candidate already contains a known source hold.
                    # Do not repeat its expensive qualification on unchanged
                    # automatic ticks. Uncertain effects retain inspect instead.
                    self.diagnostics.state['batch']['retry']['mode'] = 'changed-inputs'
                self.diagnostics.record('ecosystem-stage',status,commandId=stage_id,preserve_last_failure=True)
                from .update_health import retain_preparation
                retain_preparation(self.diagnostics.state)
                await self.publish(phase='interrupted' if interrupted else 'error',pendingRelease=None,
                    detail='Component update preparation '+('was interrupted' if interrupted else 'failed')+'. Current components remain active.',
                    error=failure_guidance(failure) or 'Ecosystem update '+('interrupted' if interrupted else 'failed')+' during '+failure['phase'].replace('-',' ')+'. Current sources remain active; no conversation work was replayed.')
                if not isinstance(error,Exception):raise
                return
        await self.activate()

    async def stage_cache_sources(self, stage, rows):
        # Each checkout belongs to the inactive candidate. Independent Git
        # downloads may overlap, but writes to the same checkout serialize.
        # Dependency resolution, qualification and promotion still follow them.
        semaphore = asyncio.Semaphore(4)
        targets = {}
        async def stage_one(row):
            target = (stage / 'foundation' / row['path']).resolve()
            lock = targets.setdefault(target, asyncio.Lock())
            async with semaphore, lock:
                try:
                    await prepare(row)
                except Exception as error:
                    # Policy/identity checks between subprocesses also need a
                    # primary receipt before sibling cancellation is recorded.
                    failure = self.diagnostics.state.get('lastFailure', {})
                    if failure.get('attemptId') != self.diagnostics.state.get('attemptId'):
                        from .update_diagnostics import exception_facts
                        self.diagnostics.record('ecosystem-source-preflight', 'failed', **exception_facts(error))
                    raise
        async def prepare(row):
            from .staged_sources import owned_git_checkout, owned_path, require_exclusive_registry_owner
            target=stage/'foundation'/row['path']
            async def guard():
                await owned_git_checkout(target, stage/'foundation')
                require_exclusive_registry_owner(stage/'foundation/registry.json', row['url'], row['ref'], target)
            await guard()
            phase='ecosystem-source-preflight'
            current=await self.diagnostics.run(phase,process,'git','rev-parse','HEAD',cwd=target)
            dirty,artifacts=await self.diagnostics.run(phase,cache_changes,target)
            if dirty or current!=row['current']: raise ValueError('Source changed since check')
            options = await cache_checkout_options(target)
            meta=owned_path(target/'.amplifier_cache_meta.json', stage/'foundation')
            data=json.loads(meta.read_text())
            if source_key(data['git_url'], data.get('ref') or 'HEAD') != source_key(row['url'], row['ref']):
                raise ValueError('Source identity changed since check')
            if artifacts:
                # Restore only verified tracked artifacts in this copy.
                # Rechecking here also protects edits made after check.
                await guard()
                await self.diagnostics.run(phase,process,'git',*options,'--literal-pathspecs','-c','core.hooksPath=/dev/null','restore','--source=HEAD','--worktree','--',*artifacts,cwd=target)
            await self.publish(detail='Downloading '+row['label']+'…')
            await guard()
            try:
                await self.diagnostics.run('ecosystem-fetch',process,'git','-c','core.hooksPath=/dev/null','fetch','--depth=1',row['url'],row['latest'],cwd=target)
            except Exception as error:
                # Only this exact pre-checkout boundary may be classified as a
                # settled source acquisition failure by partial preparation.
                error.source_fetch_failed = True
                raise
            await guard()
            await self.diagnostics.run('ecosystem-checkout',process,'git',*options,'-c','core.hooksPath=/dev/null','checkout','--detach',row['latest'],cwd=target)
            data.update(commit=row['latest'],cached_at=time.strftime('%Y-%m-%dT%H:%M:%S'))
            owned_path(meta, stage/'foundation').write_text(json.dumps(data))
        tasks = [asyncio.create_task(stage_one(row)) for row in rows]
        try:
            await asyncio.gather(*tasks)
        except BaseException:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

    async def validate(self,stage,release):
        with update_uv_cache.candidate_cache(self.home, self.diagnostics):
            return await UpdateManager._validate(self,stage,release)

    async def _validate(self,stage,release):
        from .runtime_environment import stage as stage_runtime, receipt_directory
        from .runtime_qualification import freeze, prepare_overrides, verify_recorded
        receipt=receipt_directory(self.home,release)
        fresh=not (receipt/'runtime.lock').exists()
        project=await stage_runtime(self, release, [row for row in self.inventory if row.get('eligible') and (row.get('status') == 'update' or
            (row.get('kind') == 'runtime dependency' and row.get('status') == 'current'))], finalize=not fresh)
        # Profile preparation follows the current app offering. Saved chats and
        # workspace-specific overlays qualify lazily when explicitly resumed.
        # Scanning historical workspace/bundle pairs made a catalog-only update
        # run dozens of identical dependency installers. Do not restore that loop.
        from .bundles import offered_profiles, profile_candidates
        from .host.config import read_config
        workspace = stage / 'qualification-workspace'
        workspace.mkdir(exist_ok=True)
        config = read_config(workspace, home=stage, shared_home=stage/'shared-config', global_only=True)
        profiles = profile_candidates(config) if fresh else offered_profiles(config)
        if not profiles:
            raise ValueError('No app-level conversation profiles are configured')
        configs = [(str(workspace), profile) for profile in profiles]
        profiles_file = stage / 'profiles.json'
        write_private(profiles_file, json.dumps(profiles))
        from .update_sources import store_environment, worker_supports_shared, adopt_clean_sources
        if fresh:
            # Materialize the candidate lock once, rather than uv-syncing before
            # every workspace probe. The serving environment is never targeted.
            await self.diagnostics.run('ecosystem-runtime-prepare', process, shutil.which('uv'), 'sync', '--locked',
                '--project', str(project), '--python', '3.13', timeout=900)
        shared = store_environment(self.home) if worker_supports_shared(project) else {}
        from .host.config import worker_environment
        env={**worker_environment(),**shared,'AMPLIFIER_WEB_HOME':str(stage),'AMPLIFIER_HOME':str(stage/'shared-config'),'AMPLIFIER_UNIFIED_RELEASE':'','AMPLIFIER_INSTALL_PREPARATION':uuid.uuid4().hex,
             'AMPLIFIER_UPDATE_ATTEMPT':self.diagnostics.state.get('attemptId', '')}
        if not shared:
            env.pop('AMPLIFIER_SOURCE_STORE', None)  # Preserve pinned/older workers' legacy source contract.
        async def probe(project, *, refresh=False):
            qualified=fresh or (receipt/'runtime-installed.json').exists()
            overrides=await self.diagnostics.run('ecosystem-runtime-policy',prepare_overrides,project,receipt/'runtime-install-overrides.txt',owned_root=self.home) if qualified else Path(__file__).parent/'runtime_deps/compatibility.txt'
            command=[shutil.which('uv'),'run','--locked','--no-sync','--project',str(project),'--python','3.13','python',str(Path(__file__).with_name('update_probe.py'))]
            flags=['--install-overrides',str(overrides)] if qualified else []
            probe_env = dict(env)
            if not qualified:
                # Historical workers lack the explicit installer-policy argument.
                probe_env['UV_OVERRIDE'] = str(overrides)
            flags.append('--global-only')
            if refresh:
                # Verify this actual collected batch before its one installation.
                await self.diagnostics.run('ecosystem-prepare', process, *command, str(workspace), profiles[0],
                    *flags, '--profiles', str(profiles_file),
                    '--checked-sources', str(receipt/'runtime-sources.json'), env=probe_env, timeout=900)
                return
            completed=0
            progress={'attemptId':getattr(self.diagnostics,'state',{}).get('attemptId'),
                      'phase':'prepare' if refresh else 'compatibility','total':len(configs),
                      'completed':0,'startedAt':time.time()}
            await self.publish(probeProgress=progress)
            async def run_one(workspace, bundle):
                nonlocal completed
                async with semaphore:
                    await self.diagnostics.run('ecosystem-prepare' if refresh else 'ecosystem-compatibility',process,*command,workspace,bundle,*flags,
                        env=probe_env,timeout=900)
                    completed+=1
                    await self.publish(probeProgress={**progress,'completed':completed,'lastCompletedAt':time.time()})
            # Installers remain serial. After freezing, mount isolated workers
            # without uv sync or Foundation dependency installation.
            if not refresh:
                flags.append('--read-only')
            semaphore = asyncio.Semaphore(1 if refresh else 4)
            tasks = [asyncio.create_task(run_one(*config)) for config in sorted(configs)]
            try:
                await asyncio.gather(*tasks)
            finally:
                for task in tasks:
                    if not task.done(): task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
        if fresh:
            from .runtime_policy_reconciliation import reconcile, policy_inputs, reuse_collection
            from .runtime_source_alignment import align_checked_sources
            collection_inputs = None
            async def collect_source_policy():
                nonlocal collection_inputs
                target = stage / 'selected-source-declarations.json'
                overrides = await self.diagnostics.run('ecosystem-runtime-policy', prepare_overrides,
                    project, receipt / 'runtime-install-overrides.txt', owned_root=self.home)
                command = [shutil.which('uv'), 'run', '--locked', '--no-sync', '--project', str(project),
                           '--python', '3.13', 'python', str(Path(__file__).with_name('update_probe.py'))]
                before = policy_inputs(stage, project)
                await self.diagnostics.run('ecosystem-source-policy', process, *command, str(workspace), profiles[0],
                    '--install-overrides', str(overrides), '--global-only', '--profiles', str(profiles_file),
                    '--source-policy-only', str(target), env=env, timeout=900)
                collection_inputs = before if policy_inputs(stage, project) == before else None
                return json.loads(target.read_text())
            # Availability inventory can contain only the checked tier.
            # Reconciliation admits work from the current full runtime inventory.
            selection = await self.diagnostics.run('ecosystem-source-policy', reconcile, self, stage, project, collect_source_policy)
            # Source paths first materialized during composition were absent
            # from the early cache scan. Align them before a fresh composition;
            # never install the batch captured before a binding/checkout moved.
            selection = await reuse_collection(self, stage, project, selection, collection_inputs)
            if selection is None:
                selection = await collect_source_policy()
            await self.diagnostics.run('ecosystem-source-preflight', align_checked_sources,
                                       self, stage, selection=selection)
            if shared:
                await self.diagnostics.run('ecosystem-source-sharing', adopt_clean_sources,
                                           self, stage, selection=selection)
            await self.publish(detail='Preparing changed dependencies in an isolated runtime…')
            # The refresh activator dies with each short-lived probe process.
            # Capture after dynamic module installation, then recreate an
            # ordinary resolver against the frozen graph before activation.
            await probe(project,refresh=True)
            # The composed characteristics distinguish reusable partial bundles
            # from roots. Compatibility probes mount only complete profiles.
            profiles = offered_profiles(config)
            if not profiles:
                raise ValueError('No complete app-level conversation profiles are configured')
            configs = [(str(workspace), profile) for profile in profiles]
            await self.publish(detail='Recording and verifying the exact worker dependencies…',probeProgress=None)
            project=await freeze(self,release,project)
        if (project/'.venv').exists():
            await asyncio.to_thread(verify_recorded,project,receipt)
        await self.publish(detail=f'Checking {len(configs)} offered conversation profiles…')
        await probe(project)
        await asyncio.to_thread(verify_recorded,project,receipt)
        from .runtime_profiles import configuration_key
        write_private(stage/'profiles-qualified.json', json.dumps({'profiles': profiles,
            'configuration': configuration_key(config.settings, self.home, stage)}))

    def _activation_inputs(self, target):
        """What can change the result of a prepared update's validation at activation.

        The prepared update, the settings and the selection that also hold a
        failed preparation (update_health.preparation_key), and the person's
        keys file, which that validation reads: its time and size, never its
        content. Settings that cannot be read are one state of their own.
        """
        from .session_files import amplifier_home
        from .update_health import preparation_key
        # Total: the owner's pass asks this at every pass while a failure is held.
        try:
            witness = preparation_key(self, [])
        except Exception as error:
            witness = 'unreadable ' + type(error).__name__
        try:
            info = (amplifier_home() / 'keys.env').stat()
            keys = (info.st_mtime_ns, info.st_size)
        except Exception:
            keys = None
        return target, witness, keys

    def _activation_held(self, target):
        """This update's validation at activation failed, and nothing that can change its result has changed.

        The owner's pass asks, so that time alone does not repeat minutes of
        probe processes (tick). A person's own request does not ask. The fact
        is in memory only: a start of the app validates once more.
        """
        failed = getattr(self, '_activation_failed', None)
        return failed is not None and failed == self._activation_inputs(target)

    async def activate(self, rollback=False):
        async with self.service.runtime_lifecycle():
            await self._activate(rollback)

    async def _activate(self, rollback=False):
        if self.lock.locked() or self.awaiting_restart(): return
        async with self.lock:
            if self.closed: return
            from .update_health import pending_preparation
            if pending_preparation(self.diagnostics.state):
                raise ValueError('The interrupted component preparation must be reconciled before activation or rollback.')
            pointer=active_release(self.home)
            state=self.service.state['updates']
            retrying_rollback=rollback and 'pendingRollback' in state
            target=state.get('pendingRollback') if retrying_rollback else pointer.get('previous') if rollback else state.get('pendingRelease')
            if target is not None and (not isinstance(target,str) or not re.fullmatch(r'[a-f0-9]{32}',target)):
                raise ValueError('Invalid ecosystem release identity')
            if rollback and not retrying_rollback and 'previous' not in pointer: return
            if not rollback and not target: return
            if rollback and (not retrying_rollback or self.diagnostics.state.get('kind')!='rollback'):
                self.diagnostics.begin('rollback',target)
            if target and not (self.directory/'releases'/target/'validated.json').exists():
                raise ValueError('This ecosystem release has not passed validation')
            if target:
                marker=json.loads((self.directory/'releases'/target/'validated.json').read_text())
                if marker.get('hostVersion')!=__import__('amplifier_web').__version__:
                    from .session_files import amplifier_home
                    from .update_health import STALE_STAGE_KEYS
                    from .update_storage import refresh_stage_keys, remove_stage_keys
                    target_stage = self.directory / 'releases' / target
                    # Taken before the keys file is read: a change during the
                    # validation is then a change for the owner's next pass.
                    inputs = None if rollback else self._activation_inputs(target)
                    refused = refresh_stage_keys(self.home, target_stage, amplifier_home())
                    if refused is not None and remove_stage_keys(self.home, target_stage) is not None:
                        # One rule, and it keeps no memory: qualification never
                        # reads a copy that this refresh did not write. The stage
                        # may hold such a copy (of a keys file that the person
                        # has removed or changed since), and it cannot be removed
                        # now. No pointer, runtime or receipt was changed, so this
                        # is not a failed attempt and the phase stays, as when a
                        # switch waits for work. The next request tries again.
                        # Said once: the owner's pass comes here every minute.
                        # So an earlier failed validation must not hold the
                        # owner now (_activation_held): this request validated
                        # nothing, and only the owner's pass finds the end of
                        # this refusal by itself.
                        if not rollback:
                            self._activation_failed = None
                        if state.get('error') != STALE_STAGE_KEYS:
                            await self.publish(error=STALE_STAGE_KEYS)
                        return
                    if state.get('error') == STALE_STAGE_KEYS:
                        await self.publish(error=None)  # That refusal is over.
                    try:
                        # A refused refresh is not an update failure. Validation
                        # then runs without a keys file in the stage.
                        await self.validate(target_stage, target)
                    except asyncio.CancelledError:
                        raise  # Qualification may still have a reader; keep the copy.
                    except Exception:
                        remove_stage_keys(self.home, target_stage)
                        if not rollback:
                            self._activation_failed = inputs  # See _activation_held.
                        raise
                    else:
                        remove_stage_keys(self.home, target_stage)
                        if not rollback:
                            self._activation_failed = None
                    marker['hostVersion']=__import__('amplifier_web').__version__
                    write_private(self.directory/'releases'/target/'validated.json',json.dumps(marker))
            from .update_partial import read as partial_read, verify_pointer, adopted_items, public_results
            partial = partial_read(self.directory / 'releases' / target) if target and not rollback else None
            if pointer.get('current') != target:
                verify_pointer(self, partial)
            hot = bool(target and marker.get('generationSchema') == 1 and marker.get('workerProtocol') == 1
                       and callable(getattr(self.service.runtime, 'promote_generation', None)))
            self.diagnostics.capture_activation_retry(target, rollback=rollback)
            async with self.service.lock:
                if self.service.closed:
                    return
                if not hot and self.busy():
                    self.service.state['updates'].update(detail='Waiting for active work and calls to finish. Try rollback again when idle.' if rollback else 'Update ready; it will activate when work and calls finish.')
                    self.service._publish_changes(globals={'updates','settings'})
                    return
                if rollback:
                    # Persist the requested target before promotion.  A retry
                    # after committing active.json must not reinterpret the
                    # swapped pointer and roll forward again.
                    self.service.state['updates']['pendingRollback'] = target
                self.service.state['updates'].update(phase='activating',detail='Switching ecosystem version…')
                self.service._publish_changes(globals={'updates','settings'})
            candidate = None
            # True while the page has the publication of the new selection and
            # not yet the last publication of this switch: the result of its
            # own read of the proof, or its own failure.
            unsettled = False
            try:
                # New work is gated during this short phase. Old sessions remain
                # durable.  The pointer is committed only after replacement
                # construction succeeds, and the live manager is replaced before
                # its terminal close can leave this host unable to admit work.
                candidate = None if hot else self.service.runtime_candidate()
                # A promotion may have committed before a runtime replacement
                # or state publication failed.  Its durable pointer already
                # retains the rollback identity, so finish it without
                # rewriting previous to the target itself.
                if pointer.get('current') != target:
                    if not rollback and 'appSelection' in marker:
                        # Settings can change while a qualified candidate waits
                        # for activation. Bind new promotions to the selection
                        # actually checked; never reinterpret rollback or an
                        # already committed uncertain promotion as fresh intent.
                        from .update_sequence import verify_app_selection, StaleAppSelection
                        try:
                            verify_app_selection(self, [{'appSelection': marker['appSelection']}],
                                                 stage=self.directory / 'releases' / target)
                        except StaleAppSelection:
                            if active_release(self.home) != pointer:
                                raise  # A raced/uncertain pointer is not safe retirement.
                            await self.service.discard_runtime(candidate)
                            candidate = None
                            self.inventory = []
                            write_private(self.directory / 'inventory.json', '[]')
                            self.diagnostics.record('ecosystem-selection-retired', 'succeeded')
                            await self.publish(pendingRelease=None, available=0, includedCanProceed=False,
                                phase='checked', error=None, sequence={'stage': 'application', 'install': False},
                                items=[row for row in state.get('items', []) if row.get('id') == 'application'],
                                detail='Component choices changed while this candidate waited. Check for updates to qualify the current configuration; the unused candidate and its receipts are preserved.')
                            return
                    write_private(self.directory/'active.json',json.dumps({'current':target,'previous':pointer.get('current'),'at':time.time()}))
                    if not rollback and 'pendingRollback' in self.service.state['updates']:
                        # The pointer is now the durable rollback authority for
                        # this normal promotion.  Discard an older failed
                        # rollback intent before any later publication can fail.
                        async with self.service.lock:
                            self.service.state['updates'].pop('pendingRollback', None)
                            self.service._publish_changes(globals={'updates','settings'})
                if hot:
                    def protected(sid):
                        voice = self.service.state.get('voice', {})
                        from .update_drain import retains_worker
                        return (retains_worker(self.service, self.service.runtime, sid) or
                            voice.get('sessionId') == sid and voice.get('status') not in {None, 'idle', 'ended', 'error', 'disconnected'})
                    self.service.runtime.promote_generation(target, protected=protected)
                else:
                    await self.service.replace_runtime(candidate)
                if rollback:
                    async with self.service.lock:
                        self.service.state['settings']['updates']['autoInstall'] = False
                tier = marker.get('updateTier') if target else None
                current_items = self.service.state['updates'].get('items', [])
                items = adopted_items(current_items, partial) if partial else (
                    [row for row in current_items if row.get('id')=='application'] if rollback else
                    [{**row, **({'status':'current','current':row['latest']} if row.get('status')=='update' and row.get('id')!='application' and row.get('kind')!='smart tool' and (not tier or row.get('updateTier') == tier) else {})} for row in current_items])
                if partial:
                    from .update_partial import adopted_inventory
                    self.inventory = adopted_inventory(self.inventory, partial)
                    write_private(self.directory / 'inventory.json', json.dumps(self.inventory), owned_root=self.home)
                sequence = self.service.state['updates'].get('sequence')
                if tier == 'included' and not rollback:
                    from .safe_update import included_result
                    included_result(self, items, promoted=True)
                if sequence:
                    from .update_sequence import summary
                    tier_summary = summary([row for row in items if row.get('updateTier') == tier])
                    sequence = {**sequence}
                    if rollback:
                        sequence.update(stage='complete', install=False)
                        sequence.pop('nextStage', None)
                    elif tier == 'included':
                        sequence.update(nextStage='other', included=tier_summary)
                    elif any(row.get('kind')=='smart tool' and row.get('status')=='update' for row in items):
                        sequence.update(nextStage='other', other=tier_summary)
                    else:
                        sequence.update(stage='complete', install=False, other=tier_summary)
                # The selection is new, and its proof is not read yet: the held
                # proof is the one of the generation before. Until the read
                # below ends, the page gets no result from an earlier component
                # failure record (update_health.publish).
                self._update_proof_unread = True
                await self.publish(phase='installed',release=target,pendingRelease=None,canRollback=True,items=items,error=None,
                    componentResults=public_results(partial, adopted=True),
                    includedCanProceed=False,
                    **({'sequence': sequence} if sequence else {}),
                    available=sum(row.get('status')=='update' for row in items),installedAt=time.time(),detail='Amplifier went back to the earlier components. "Keep Amplifier up to date" is now off, so that the same update is not installed again by itself.' if rollback else 'Update ready for new work. Active conversations adopt it after their current work finishes.')
                unsettled = True  # The page has the new selection now.
                if rollback or 'pendingRollback' in self.service.state['updates']:
                    async with self.service.lock:
                        self.service.state['updates'].pop('pendingRollback', None)
                        self.service._publish_changes(globals={'updates','settings'})
                self.diagnostics.clear_failure()
                self.diagnostics.record('ecosystem-rollback' if rollback else 'ecosystem-activation','succeeded')
                from .update_health import refresh as refresh_health
                await refresh_health(self)
                self._update_proof_unread = False  # This switch's own read ended, with or without a proof.
                await self.publish()  # Publish settled attention, not just the preceding installed status.
                unsettled = False
                self.inventory=[]
                if self.cleanup_task is None or self.cleanup_task.done():
                    self.cleanup_task = asyncio.create_task(self.retire_storage())
                # The release is live and the replacement runtime installed.
                # A stale inventory is recoverable; it must not rewrite this
                # successful activation as an installation failure.
                try:
                    write_private(self.directory/'inventory.json','[]')
                except OSError:
                    pass
            except asyncio.CancelledError:
                self._update_proof_unread = False  # The mark ends with the switch.
                await self.service.discard_runtime(candidate)
                raise
            except Exception as error:
                self._update_proof_unread = False  # The mark ends with the switch.
                await self.service.discard_runtime(candidate)
                from .update_diagnostics import exception_facts
                self.diagnostics.record('ecosystem-activation','failed',**exception_facts(error))
                await self.publish(phase='error',error='Could not activate the ecosystem update; review its diagnostic receipt before retrying.')
                unsettled = False  # The page has this switch's own failure now.
            finally:
                if unsettled:
                    # The switch ends, and the page has its selection without its
                    # last publication: it was cancelled in its own read, in the
                    # publication after that read, or in the publication of its
                    # own failure. The page can then hold "Checking components"
                    # with nothing that checks. Nothing can be published for
                    # sure in a task that ends this way. So the switch asks the
                    # update owner for the read: the owner's next pass that no
                    # hold stops reads and publishes (tick), as after a settings
                    # change. The owner is woken for it.
                    self._update_health_initial = True
                    self.wakeup.set()

    async def installSmartTools(self):
        from .smart_tool_update_batch import stage
        await stage(self)

    async def activateSmartTools(self):
        from .smart_tool_update_batch import activate
        await activate(self)

    async def smartToolRetry(self, identity):
        from .smart_tool_update_batch import retry
        await retry(self, identity)

    async def smartToolSkip(self, identity):
        from .smart_tool_update_batch import skip
        await skip(self, identity)

    async def smartToolRollback(self, identity):
        if self.lock.locked(): raise ValueError('Wait for the current update to finish.')
        async with self.lock:
            state = self.service.state['updates']
            pending = list(state.get('pendingSmartTools', []))
            if any(state.get(key) for key in ('pendingApp', 'pendingRelease')) or any(not item.get('failure') for item in pending):
                raise ValueError('Finish or inspect the pending update before restoring a tool.')
            from .managed_runner_updates import rollback as runner_rollback
            if await asyncio.to_thread(runner_rollback, self.service, identity):
                async with self.service.lock:
                    self.service.state['settings']['updates']['autoInstall'] = False
                await self.publish(phase='installed', error=None, detail='The previous test runner is selected for future checks. Existing checks keep their exact runner.')
                self.inventory = []
                return
            row = next((item for item in self.service.smart_tools.state['installations'] if item['id'] == identity), {})
            previous = row.get('previousInstallationId')
            if not previous: raise ValueError('No previous Smart Tool revision is available.')
            self.diagnostics.begin('smart-tools')
            await self.publish(pendingSmartTools=[*pending, {'previous': identity, 'target': previous}], phase='staged', sequence={**state.get('sequence', {}), 'install': False})
            async with self.service.lock:
                self.service.state['settings']['updates']['autoInstall'] = False
        await self.activateSmartTools()

    async def rollback(self):
        await self.activate(rollback=True)

    def check_due(self):
        """Why the update owner checks by itself now: 'start', 'timed' or None.

        The one rule for a check that time causes. tick() asks only where a
        timed check is allowed, so a host start adds no other place and no
        retry. A check that continues an update sequence, and the new check of
        a selection that changed, have their own rules and do not ask here.
        'start': when this host started, the last check or attempt was at least
        HOST_START_CHECK_AFTER_HOURS old, and no check has passed its admission
        since (_check). Any admitted check ends it, also a person's own and one
        that fails. The end is a fact of this owner, not clock arithmetic: a
        clock that is set back after the start brings no second start check.
        'start' is asked first, so every check at a host start asks again and
        does not reuse the answers that are saved for the timed interval.
        """
        settings = self.service.state['settings'].get('updates', {})
        if not settings.get('autoCheck', True):
            return None
        state = self.service.state['updates']
        last = max(state.get('lastCheck') or 0, state.get('lastAttempt') or 0)
        if (getattr(self, '_start_check_open', False)
                and self._host_started - last >= HOST_START_CHECK_AFTER_HOURS * 3600):
            return 'start'
        if time.time() - last >= settings.get('intervalHours', DEFAULT_CHECK_INTERVAL_HOURS) * 3600:
            return 'timed'
        return None

    async def tick(self):
        if getattr(self, '_manual_handoff_commit_unknown', False):return
        settings=self.service.state['settings'].get('updates',{})
        state=self.service.state['updates']
        from .safe_update import adoption_target, enroll, waiting, component_restart
        await enroll(self)
        from .update_drain import observe_waiters
        await observe_waiters(self)
        if self.awaiting_restart():
            # See loop: this pass did not come to the first read of this host.
            self._first_read_held = getattr(self, '_update_health_initial', False)
            return
        if getattr(self, '_update_health_initial', False):
            self._update_health_initial = False
            from .manual_app_handoff import verify_current
            try:
                await verify_current(self, full=True)
            except (ValueError, OSError, RuntimeError, KeyError, TypeError):
                self._manual_handoff_verified = None
            from .update_health import refresh as refresh_health
            await refresh_health(self)
            await self.publish()
        from .update_drain import finish
        drain = state.get('drain') or {}
        if drain.get('phase') in {'draining', 'replacing', 'releasing'}:
            installed = state.get('applicationResult') or {}
            if all(installed.get(key) == adoption_target(drain).get(key) for key in ('attemptId', 'revision')):
                await finish(self, 'installed')
            elif not state.get('pendingApp') or state.get('phase') in {'error', 'interrupted'} or drain.get('hostInstanceId') != self.service.instance_id:
                await finish(self, 'withdrawn')
        if (getattr(self, '_storage_policy_due', False) or time.time() - getattr(self, '_storage_last_pass', 0) >= 3600) and (not self.cleanup_task or self.cleanup_task.done()):
            self._storage_policy_due = False
            self._storage_last_pass = time.time()
            self.cleanup_task = asyncio.create_task(self.retire_storage())
        usage = getattr(self.service.runtime, 'generation_usage', lambda: [])()
        adoption = {'activeWorkers': len(usage), 'pendingWorkers': sum(row['updatePending'] for row in usage)}
        if state.get('adoption') != adoption:
            if state.get('adoption', {}).get('pendingWorkers') and not adoption['pendingWorkers']:
                if not self.cleanup_task or self.cleanup_task.done():
                    self.cleanup_task = asyncio.create_task(self.retire_storage())
            await self.publish(adoption=adoption)
        blockers = (self.app_blockers() if state.get('pendingApp')
                    else self.blockers()) if self.requires_idle(state) else []
        if state.get('blockers', []) != blockers:
            await self.publish(blockers=blockers)
        if (state.get('applicationIntent') or {}).get('scope') == 'app':
            # This explicit request replaced any older whole-install intent.
            # Preserve other prepared/uncertain work, but do not activate it.
            if state.get('phase') in {'error', 'interrupted'}:
                return  # Retained scope never authorizes retry of failed effects.
            if state.get('pendingApp'):
                if self.check_due():
                    await self.check(fresh=True, automatic=True)
                await self.app()
            elif state.get('appAvailable'):
                await self.app()
            elif due := self.check_due():
                # A future ordinary check is a new policy cycle, not continuation
                # of the superseded whole-install command or its stale inventory.
                await self.check(fresh=due == 'start', automatic=True)
            return
        if pending_tool_activations(state) and not prepared_tool_activations(state):
            if due := self.check_due():
                await self.check(fresh=due == 'start', automatic=True)
            from .windows_app_recovery import automatic_retry_held
            if (state.get('phase') not in {'error', 'interrupted'} and not state.get('pendingRelease')
                    and not automatic_retry_held(state) and has_new_app_target(self, state)
                    and (state.get('pendingApp') or settings.get('autoInstall', True))):
                await self.app()
                return
            if (state.get('phase') not in {'error', 'interrupted'} or any(
                    row.get('runnerUpdate') and not row.get('failure') for row in state['pendingSmartTools'])):
                await self.activateSmartTools()
            return
        if state.get('pendingApp'):
            if state.get('phase') in {'error', 'interrupted'}:return
            if self.check_due() and not waiting(state) and not component_restart(drain, state['pendingApp']):
                await self.check(fresh=True, automatic=True)
            await self.app()
            return
        if state.get('pendingRelease'):
            from .update_health import pending_preparation
            if pending_preparation(self.diagnostics.state):return
            if self._activation_held(state['pendingRelease']):return
            await self.activate()
            return
        if state.get('initialPreparation') and state.get('sequence', {}).get('stage') == 'complete':
            await self.publish(initialPreparation=False)
        sequence = state.get('sequence', {})
        from .update_health import pending_preparation, preparation_owner_stopped, reconcile_preparation
        pending = pending_preparation(self.diagnostics.state)
        reconciled = False
        if (pending and preparation_owner_stopped(self, pending.get('preparation') or {})
                and not self.lock.locked()):
            async with self.lock:
                reconciled = reconcile_preparation(self)
                if reconciled:
                    # The retired attempt may have queued a later tier or an
                    # install before it was interrupted. Only fresh discovery
                    # or a new explicit request can authorize the next install.
                    sequence = {**state.get('sequence', {}), 'install': False}
                    sequence.pop('nextStage', None)
                    await self.publish(sequence=sequence, phase='checked')
        if pending_preparation(self.diagnostics.state):
            # Health, drain, storage, worker adoption and independent app/tool
            # paths above still run. No component availability/install is replayed.
            if due := self.check_due():
                await self.check(tier='application', fresh=due == 'start', automatic=True)
            from .windows_app_recovery import automatic_retry_held
            if state.get('appAvailable') and settings.get('autoInstall', True) and not automatic_retry_held(state):
                await self.app()
            return
        if reconciled and settings.get('autoCheck', True):
            # Retiring a fence is not permission to install stale inventory.
            # Fresh discovery still obeys the ordinary install/admission policy.
            await self.check(fresh=True, automatic=True)
        elif sequence.get('nextStage') and state.get('phase') not in {'error','interrupted'}:
            # App-first checking deliberately skips component discovery. After
            # the new app starts, refresh those refs rather than inheriting a
            # cached result from before its required components were published.
            # Refresh later tiers too: an earlier tier may have stopped before
            # checking them, leaving only results from a previous update cycle.
            await self.check(tier=sequence['nextStage'], install=sequence.get('install', False),
                             fresh=True, automatic=True)
        elif due := self.check_due():
            await self.check(fresh=due == 'start', automatic=True)
        def admitted():
            state=self.service.state['updates']
            settings=self.service.state['settings'].get('updates',{})
            managed_preview=state.get('appAvailable') and state.get('application',{}).get('canInstall') is False
            sequence = state.get('sequence', {})
            install_intent = (sequence.get('install') if sequence.get('manualHandoff')
                              else settings.get('autoInstall', True) or sequence.get('install'))
            return not managed_preview and install_intent and state.get('available',0) and state.get('phase') == 'available'
        if admitted():
            # A newly checked desired graph still goes through install's input
            # admission. Elapsed time never replays a settled failed preparation.
            await self.install_checked(admitted, automatic=True)
            return
        state=self.service.state['updates']
        if prepared_tool_activations(state) or (self.independent_tool_results(state) and (
                state.get('phase') not in {'error', 'interrupted'} or any(
                    row.get('runnerUpdate') and not row.get('failure') for row in state['pendingSmartTools']))):
            await self.activateSmartTools()

    async def wait_for_work(self, delay):
        try:
            await asyncio.wait_for(self.wakeup.wait(), delay)
        except TimeoutError:
            pass

    async def loop(self):
        await self.wait_for_work(3)
        woke = False
        while not self.closed:
            self.wakeup.clear()
            state = self.service.state['updates']
            sequence = state.get('sequence', {})
            before = (state.get('phase'), sequence.get('stage'), sequence.get('nextStage'))
            previous_event=self.diagnostics.state.get('latest',{}).get('id')
            try:
                await self.tick()
            except asyncio.CancelledError: raise
            except Exception as error:
                from .update_diagnostics import exception_facts, failure_guidance
                last=self.diagnostics.state.get('latest',{})
                if last.get('status') not in {'failed','interrupted'} or last.get('id')==previous_event:
                    self.diagnostics.record('background-update','failed',**exception_facts(error))
                phase=self.diagnostics.state['latest']['phase']
                await self.publish(phase='activating' if self.service.state['updates'].get('pendingRestart') else 'error',error=failure_guidance(self.diagnostics.state.get('lastFailure', {})) or 'Background update failed during '+phase.replace('-',' ')+'. Review its diagnostic receipt; no work was replayed.')
            from .windows_launcher_follow import consider
            await consider(self)  # After the except: a failing tick must not starve an open launcher request.
            # A new host has no proof of the installed components until a pass
            # reads it (tick), and a hold (awaiting_restart) keeps a pass from
            # that read. The hold can end above, for example when a launcher
            # request closes, and nothing else wakes this loop then. Without a
            # wake the page keeps the text of a host with no proof for the 60
            # seconds below. One wake for a pass that the hold stopped. A pass
            # that this wake started asks for no second one: when its hold is
            # on again, the loop waits its 60 seconds.
            held, self._first_read_held = getattr(self, '_first_read_held', False), False
            woke = held and not woke and not self.closed and not self.awaiting_restart()
            if woke:
                self.wakeup.set()
            state = self.service.state['updates']
            sequence = state.get('sequence', {})
            from .update_health import pending_preparation
            # Only a changed stage can request immediate continuation. Retained
            # nextStage intent cannot rearm a held/no-progress preparation.
            if (not self.closed and sequence.get('nextStage') and state.get('phase') == 'installed'
                    and before != (state.get('phase'), sequence.get('stage'), sequence.get('nextStage'))
                    and not pending_preparation(self.diagnostics.state)
                    and not self.lock.locked() and not (self.check_task and not self.check_task.done())):
                self.wakeup.set()
            # A pending external notification can make wait_for_work return
            # immediately; give HTTP and other owners a turn even in that case.
            await asyncio.sleep(0)
            await self.wait_for_work(60)

    async def retire_storage(self):
        # Storage maintenance is outside the activation request. Worker leases
        # protect active generations; unrelated work need not delay reclamation.
        await asyncio.sleep(10)
        if self.closed or self.awaiting_restart():
            return
        started = time.monotonic()
        try:
            from .update_storage import scrub_stage_keys
            await scrub_stage_keys(self)
        except Exception:
            pass  # A later pass tries again. It never defers the maintenance below.
        try:
            # Tool result retention is its own existing promise, independent of
            # whether unused-code cleanup is enabled in Settings.
            smart_tools = getattr(self.service, 'smart_tools', None)
            if smart_tools is not None:
                await smart_tools.expire_results()
            from .storage_cleanup import run
            storage = await run(self.service, uuid.uuid4().hex, automatic=True)
            await self.publish(storage={**storage, 'durationMs': round((time.monotonic() - started) * 1000)})
        except Exception:
            await self.publish(storage={'reason': 'Storage reclamation was deferred; existing files were kept.'})

    async def close(self):
        self.closed=True
        if self.cleanup_task and not self.cleanup_task.done():
            self.cleanup_task.cancel()
            await asyncio.gather(self.cleanup_task, return_exceptions=True)
        if self.check_task:
            self.check_task.cancel()
            await asyncio.gather(self.check_task, return_exceptions=True)
        await self.check_cache.close()
        if self.readiness_task:
            self.readiness_task.cancel()
            await asyncio.gather(self.readiness_task,return_exceptions=True)
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task,return_exceptions=True)
