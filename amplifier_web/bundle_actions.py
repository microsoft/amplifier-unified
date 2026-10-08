"""Bundle actions shared by browser controls and app_control."""
from __future__ import annotations
import copy
import shutil
import uuid
from pathlib import Path


async def perform(management, action, args):
    app = management.service
    sid = args['sessionId']
    async with app.lock:
        current = app._session(sid)
        if current.get('pendingConfiguration', {}).get('phase') in {'queued', 'applying'}:
            raise ValueError('Cancel or finish the queued module changes before changing bundles.')
        if not management.configuration_idle(current):
            raise ValueError('Finish the current turn and its workers before changing bundles.')
        current['configurationBusy'] = True
        current['bundleChange'] = {'phase': 'working', 'action': action, 'bundle': args['bundle']}
        source = copy.deepcopy(current)
        app._publish_changes(sessions={sid})
    try:
        recovering = (source.get('errorType') == 'RuntimeStartupError'
                      or source.get('failure', {}).get('category') == 'worker_startup')
        replacement = None
        if recovering and action in {'bundle.preview', 'bundle.switch'}:
            from .draft_defaults import resolve_defaults
            checked = await resolve_defaults(app.data_dir, source['workspace'], args['bundle'],
                session_id=source.get('runtimeSessionId') or source.get('nativeIdentity') or sid,
                replacement={key: source[key] for key in ('selection', 'workingDirectory') if key in source})
            expected = source.get('bundlePreview', {})
            if args.get('previewId') and (expected.get('previewId') != args['previewId'] or any(
                    expected.get(key) != checked.get(key) for key in ('bundle', 'fingerprint', 'selection'))):
                raise ValueError('The configuration changed. Preview the selected bundle again.')
            checked['previewId'] = str(uuid.uuid4())
            async with app.lock:
                app._session(sid)['bundlePreview'] = checked
            if action == 'bundle.switch':
                if not checked['modelCompatible'] and not args.get('resetModel'):
                    raise ValueError('Your pinned model is unavailable in this bundle. Select Use the new bundle’s model to continue.')
                replacement = {**checked, 'resetModel': args.get('resetModel', False)}
                await management.ensure_runtime(source, bundle_replacement=replacement)
        else:
            await management.ensure_runtime(source)
        if action == 'bundle.preview':
            result = checked if recovering else await app.runtime.control(sid, action, {'bundle': args['bundle']})
            async with app.lock:
                app._session(sid)['bundlePreview'] = result
        elif action == 'bundle.switch':
            if replacement is not None:
                result = {'bundle': args['bundle'], 'historyPreserved': True,
                    'configuration': await app.runtime.control(sid, 'configuration.inspect', {}),
                    'providers': await app.runtime.control(sid, 'configuration.providers', {})}
            else:
                result = await app.runtime.control(sid, action, args)
            if result.get('requiresModelChoice'):
                async with app.lock:
                    app._session(sid)['bundlePreview'] = result['preview']
                raise ValueError('Your pinned model is unavailable in this bundle. Select Use the new bundle’s model to continue.')
            async with app.lock:
                current = app._session(sid)
                current.update(bundle=result['bundle'], configuration=result['configuration'])
                current.pop('bundlePreview', None)
                if args.get('resetModel'): current.pop('selection', None)
                app.state.setdefault('runtimeControl', {})[sid] = {'configuration.providers': result['providers']}
            management.background(management.warm_runtime_models(sid, result['providers'].get('providers', []), result['providers'].get('catalogRevision')))
        else:
            expected = app._session(sid).get('bundlePreview', {})
            if args.get('previewId') and (expected.get('previewId') != args['previewId'] or expected.get('bundle') != args['bundle']):
                raise ValueError('Preview the selected bundle again before forking.')
            checked = await app.runtime.control(sid, 'bundle.preview', {'bundle': args['bundle']})
            if args.get('previewId') and any(checked.get(key) != expected.get(key) for key in ('fingerprint', 'selection')):
                raise ValueError('The configuration changed. Preview the selected bundle again.')
            # Refresh the original preview token too, so a failed fork can be retried.
            async with app.lock: app._session(sid)['bundlePreview'] = checked
            if not checked['modelCompatible'] and not args.get('resetModel'):
                raise ValueError('Your pinned model is unavailable in this bundle. Select Use the new bundle’s model to continue.')
            from .session_store import fork_session
            from .host.storage import SessionStore
            target = app._new_session({'title': source['title']+' · fork', 'workspace': source['workspace'], 'bundle': args['bundle']})
            identity = target['id']
            store = SessionStore.for_app(app.data_dir, source['workspace'])
            # Capture the complete current context before copying. No user work is submitted.
            live = await app.runtime.control(sid, 'history.snapshot', {})
            events = []
            async def collect(event, payload): events.append((event, payload))
            try:
                target.update(fork_session(app.data_dir, source, identity, live_messages=live.get('messages'),
                                          bundle=args['bundle'], reset_model=args.get('resetModel', False)))
                await app.runtime.start(target, collect)
                configuration = await app.runtime.control(identity, 'configuration.inspect', {})
                providers = await app.runtime.control(identity, 'configuration.providers', {})
            except BaseException:
                await app.runtime.stop(identity)
                # These are newly allocated fork directories, never source data.
                for directory in (store.directory(identity), app.data_dir/'sessions'/identity):
                    if directory.exists(): shutil.rmtree(directory)
                raise
            async with app.lock:
                from .canvas_library import fork_artifacts, remember
                from .workspace_canvas import select_session_workspace
                from .naming import persist
                remember(app.state, app.db)
                app.clients.remember_canvas()
                target.update(configuration=configuration, status='ready')
                app.state['sessions'].insert(0, target)
                fork_artifacts(app.state, sid, target, app.db)
                # A slow fork must not retarget a different chat opened meanwhile.
                if app.state.get('selectedSessionId') == sid:
                    if app.clients.record() is None:
                        app._session(sid)['draft'] = app.state['view'].get('draft', '')
                    app.state['selectedSessionId'] = identity
                    app.state['view']['draft'] = ''
                    select_session_workspace(app.state, target)
                    app.clients.restore_canvas()
                persist(app.data_dir, target)
                app.state.setdefault('runtimeControl', {})[identity] = {'configuration.providers': providers}
                app._publish_full(reason='Bundle fork adds a session and copies its artifacts')
            await app.runtime.start(target, app.on_runtime_event)
            for event, payload in events:
                if event == 'runtime.status' and payload.get('report'):
                    await app.on_runtime_event(event, payload)
            result = {'sessionId': identity, 'bundle': args['bundle'], 'historyPreserved': True}
        async with app.lock:
            app._session(sid)['bundleChange'] = {'phase': 'ready', 'action': action, 'bundle': args['bundle'],
                                                **({'sessionId': result['sessionId']} if result.get('sessionId') else {})}
            app._publish_changes(sessions={sid})
    except BaseException as exc:
        async with app.lock:
            app._session(sid)['bundleChange'] = {'phase': 'error', 'action': action, 'bundle': args['bundle'], 'error': str(exc)[:1000]}
            app._publish_changes(sessions={sid})
        raise
    finally:
        async with app.lock:
            app._session(sid)['configurationBusy'] = False
            app._publish_changes(sessions={sid})
