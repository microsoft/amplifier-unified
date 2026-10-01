"""Matched synthetic, file-backed history benchmark. Never run on live storage."""
import argparse
import asyncio
from collections import Counter
import copy
import json
import os
from pathlib import Path
import sys
import time
sys.path.insert(0, os.environ.get('AMPLIFIER_PERF_APP_ROOT', str(Path(__file__).resolve().parents[2])))


class Runtime:
    async def start(self, *args):
        pass

    async def stop(self, *args):
        pass

    async def close(self):
        pass


def fixtures(root, count):
    from amplifier_web.session_files import project_slug
    home = root / 'native'
    projects = max(1, count // 5)
    metadata = {'bundle': 'anchors', 'created': '2026-01-01T00:00:00Z', 'turn_count': 1}
    body = json.dumps({'role': 'user', 'content': 'Synthetic saved question'}) + '\n'
    marker = root / 'fixture-count.json'
    if not marker.exists():
        for number in range(count):
            project, child = divmod(number, 5)
            workspace = root / 'workspaces' / str(project)
            workspace.mkdir(parents=True, exist_ok=True)
            identity = 'saved-' + str(project) if child == 0 else 'saved-' + str(project) + '_worker-' + str(child)
            directory = home / 'projects' / project_slug(workspace) / 'sessions' / identity
            directory.mkdir(parents=True, exist_ok=True)
            (directory / 'metadata.json').write_text(json.dumps({**metadata, 'working_dir': str(workspace),
                'session_id': identity, 'name': 'Synthetic ' + identity,
                'parent_id': None if child == 0 else 'saved-' + str(project)}))
            (directory / 'transcript.jsonl').write_text(body)
        marker.write_text(json.dumps({'sessions': count, 'projects': projects}))
    elif json.loads(marker.read_text())['sessions'] != count:
        raise ValueError('Existing fixture scale differs; never reuse different inputs')
    workspace = root / 'workspaces' / '0'
    target = home / 'projects' / project_slug(workspace) / 'sessions' / 'saved-0_worker-1' / 'transcript.jsonl'
    target.write_text(body)  # Reset the same synthetic target for each variant.
    return home, workspace, target, projects


async def run(args):
    root = Path(args.root).resolve()
    if not root.is_relative_to(Path('/opt/unified-validation')):
        raise ValueError('Benchmark must run inside its disposable DTU')
    root.mkdir(parents=True, exist_ok=True)
    home, workspace, target, project_count = fixtures(root, args.sessions)
    os.environ['AMPLIFIER_HOME'] = str(home)
    from amplifier_web.service import AppService
    import amplifier_web.browser_state as browser
    import amplifier_web.session_projection as projection
    from amplifier_web.session_files import project_slug
    app_workspace = root / 'app-workspace'
    app_workspace.mkdir(exist_ok=True)
    app = AppService(root / ('app-' + args.label), Runtime(), workspace=app_workspace)
    app.subscribe()
    await app.dispatch('session.create', {'workspace': str(app_workspace)})
    app.history.index.close()
    dirty = {'*'}
    app.history.index._invalidations = lambda: (True, set(dirty))
    try:
        await app.history.refresh()
        dirty.clear()
        await app.history.refresh(force=False)
        await app.history.refresh(force=False)
        assert app.state['sharedHistory']['error'] is None
        assert sum(bool(row.get('nativeIdentity')) for row in app.state['sessions']) == args.sessions
        own = next(row for row in app.state['sessions'] if not row.get('historyManaged'))
        metrics = Counter()
        original_copy, original_json = copy.deepcopy, json.dumps
        original_index, original_persist = browser.SessionIndex.__init__, projection.persist

        def detach(value, *a, **kw):
            if isinstance(value, dict) and 'workspaces' in value and 'sessions' in value:
                metrics['catalogRowsCopied'] += len(value['sessions'])
            return original_copy(value, *a, **kw)

        def encode(value, *a, **kw):
            if isinstance(value, dict) and value.get('id') == own['id'] and 'messages' in value:
                metrics['appViewsEncoded'] += 1
            return original_json(value, *a, **kw)

        def index(self, state):
            metrics['sessionIndexBuilds'] += 1
            metrics['sessionIndexRows'] += len(state['sessions'])
            return original_index(self, state)

        def persist(data_dir, state, cache, **kw):
            ids = kw.get('session_ids')
            metrics['persistenceRowsVisited'] += len(state['sessions']) if ids is None else len(ids)
            return original_persist(data_dir, state, cache, **kw)

        copy.deepcopy, json.dumps = detach, encode
        browser.SessionIndex.__init__, projection.persist = index, persist
        result = {'label': args.label, 'sessions': args.sessions, 'projects': project_count,
                  'source': str(Path(sys.modules['amplifier_web'].__file__).parent),
                  'limitations': 'Synthetic file-backed direct history refresh; not live CPU or full idle-service measurement.'}
        try:
            phases = {}
            for phase in ('quiet', 'single-change'):
                metrics.clear()
                start, cpu = time.monotonic(), time.process_time()
                for number in range(args.rounds):
                    dirty.clear()
                    if phase == 'single-change':
                        with target.open('a') as file:
                            file.write(original_json({'role': 'assistant', 'content': 'Synthetic increment ' + str(number)}) + '\n')
                        slug = project_slug(workspace)
                        if hasattr(app.history.index, 'scan_changes'):
                            dirty.add((slug, 'saved-0_worker-1', 'transcript'))
                        else:
                            dirty.add(slug)
                    app.history.index._reconcile_at = time.monotonic() + 60
                    await app.history.refresh(force=False)
                    assert app.state['sharedHistory']['error'] is None
                    row = next(row for row in app.state['sessions'] if row.get('nativeIdentity') == 'saved-0_worker-1')
                    if phase == 'single-change':
                        assert row['nativeRevision'][1] == target.stat().st_size
                phases[phase] = {'rounds': args.rounds, 'wallSeconds': time.monotonic() - start,
                                 'cpuSeconds': time.process_time() - cpu, 'counters': dict(metrics)}
            result['phases'] = phases
        finally:
            copy.deepcopy, json.dumps = original_copy, original_json
            browser.SessionIndex.__init__, projection.persist = original_index, original_persist
        Path(args.output).write_text(original_json(result, indent=2))
        print(original_json(result, indent=2), flush=True)
    finally:
        await app.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    parser.add_argument('--label', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--sessions', type=int, default=24000)
    parser.add_argument('--rounds', type=int, default=5)
    asyncio.run(run(parser.parse_args()))
