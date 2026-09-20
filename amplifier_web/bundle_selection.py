"""Root selection policy and reversible worker-side bundle replacement."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
from pathlib import Path
import uuid

from .host.config import write_private

BUNDLE_LABELS = {
    'anchors': ('Anchors', 'General-purpose tools, instructions, and agents.'),
    'anchors-amp-dev': ('Anchors · Amplifier development', 'Anchors with Amplifier ecosystem knowledge and tooling.'),
    'work': ('Work', 'A small tool set with live delegation and managed context.'),
    'anchors-work': ('Anchors + Work', 'Anchors capabilities with Work execution and context handling.'),
}


def catalog_entry(name):
    label, description = BUNDLE_LABELS.get(name, (name, 'Registered root bundle.'))
    return {'name': name, 'value': name, 'label': label, 'description': description}


def defaults(home, workspace, app_bundle=None):
    from .shared_settings import read_yaml, settings_paths
    paths = settings_paths(workspace)
    values = {key: read_yaml(path).get('bundle', {}).get('active') for key, path in paths.items()}
    workspace_bundle = values['local'] or values['project']
    effective = workspace_bundle or app_bundle or values['global'] or 'anchors'
    source = 'workspace' if workspace_bundle else 'app' if app_bundle else 'shared'
    return {'app': app_bundle or None, 'workspace': workspace_bundle or None,
            'shared': values['global'] or None, 'effective': effective, 'source': source,
            'workspacePath': str(Path(workspace).expanduser().resolve()),
            'workspaceInherited': values['project'] or None}


def reset_controls(value, *, reset_model=False):
    """Keep the user's goal and model pin, not old bundle module toggles/budgets."""
    return {key: copy.deepcopy(value[key]) for key in ('goal', 'selection')
            if key in value and not (key == 'selection' and reset_model)}


async def preview(controls, workspace, bundle):
    from .host.config import load_config
    from .host.session import load_root_bundle, live_plan
    from .runtime_controls import public_config, validate_plan, identity
    config = load_config(workspace, session_id=controls.session.session_id)
    _, loaded, resolved = await load_root_bundle(config, bundle)
    plan, _ = live_plan(loaded.to_mount_plan())
    validate_plan(plan)
    current = controls.configuration()['plan']
    def names(value, section):
        if section == 'agents': return set(value.get(section, {}))
        if section == 'providers':
            return {row.get('instance_id') or row.get('id') or row['module'].removeprefix('provider-')
                    for row in value.get(section, []) if row.get('enabled', True)}
        return {identity(row) for row in value.get(section, []) if row.get('enabled', True)}
    changes = {}
    for section in ('tools', 'agents', 'hooks', 'providers'):
        before, after = names(current, section), names(plan, section)
        changes[section] = {'added': sorted(after-before), 'removed': sorted(before-after)}
    for section in ('orchestrator', 'context'):
        changes[section] = {'before': current.get('session', {}).get(section, {}).get('module'),
                            'after': plan.get('session', {}).get(section, {}).get('module')}
    # An unavailable pin must be an explicit choice, never a silent model change.
    saved = json.loads(controls.state_path().read_text()) if controls.state_path().exists() else {}
    selection = controls.selection or saved.get('selection')
    compatible = not selection or (selection.get('instance') or selection.get('provider')) in names(plan, 'providers')
    stamp = hashlib.sha256(json.dumps(plan, sort_keys=True, default=str).encode()).hexdigest()
    return {'bundle': bundle, 'resolved': resolved, 'fingerprint': stamp,
            'changes': changes, 'modelCompatible': compatible,
            'selection': public_config(selection), 'instructionsChange': True,
            'overridesReset': controls.state_path().with_name('configuration.json').exists(),
            'appCapabilities': list(config.app_bundles)}


class BundleTransaction:
    """Journal only files owned by this session; crash recovery restores the source."""
    def __init__(self, home, workspace, identity):
        from .host.storage import SessionStore
        self.local = Path(home) / 'sessions' / identity
        self.native = SessionStore.for_app(home, workspace).directory(identity)
        self.path = self.local / 'bundle-transition.json'
        self.paths = {f'{kind}/{name}': directory/name for kind, directory, names in (
            ('local', self.local, ('configuration.json', 'control-state.json', 'effective-configuration.json')),
            ('native', self.native, ('transcript.jsonl', 'transcript.jsonl.backup', 'metadata.json', 'metadata.json.backup')))
            for name in names}

    def begin(self):
        if self.path.exists(): raise ValueError('An earlier bundle change needs recovery before another change.')
        write_private(self.path, json.dumps({'version': 1, 'files': {
            key: path.read_text() if path.exists() else None for key, path in self.paths.items()}}))

    def restore(self):
        if not self.path.exists(): return False
        saved = json.loads(self.path.read_text())
        if saved.get('version') != 1 or set(saved.get('files', {})) != set(self.paths):
            raise ValueError('The saved bundle transition cannot be recovered automatically.')
        for key, path in self.paths.items():
            value = saved['files'][key]
            if value is None: path.unlink(missing_ok=True)
            else: write_private(path, value)
        self.path.unlink()
        return True

    def commit(self):
        self.path.unlink()

    def select(self, bundle, *, reset_model=False):
        metadata = self.native/'metadata.json'
        value = json.loads(metadata.read_text()) if metadata.exists() else {}
        value.update(bundle_name=bundle, bundle=bundle, preserve_system=False)
        write_private(metadata, json.dumps(value))
        transcript = self.native/'transcript.jsonl'
        if transcript.exists():
            rows = [json.loads(line) for line in transcript.read_text().splitlines() if line.strip()]
            # User/tool history remains intact. New root instructions replace old
            # authoritative system rows; the journal retains originals until commit.
            write_private(transcript, ''.join(json.dumps(row, ensure_ascii=False)+'\n' for row in rows
                          if row.get('role') not in {'system', 'developer'}))
        for name in ('configuration.json', 'effective-configuration.json'):
            (self.local/name).unlink(missing_ok=True)
        state = self.local/'control-state.json'
        if state.exists(): write_private(state, json.dumps(reset_controls(json.loads(state.read_text()), reset_model=reset_model)))


async def switch(worker, args):
    """Called under the worker's command lock and Foundation writer ownership."""
    worker.controls.require_idle()
    checked = await preview(worker.controls, worker.workspace, args['bundle'])
    expected = getattr(worker, 'bundle_preview', None)
    if not expected or expected['previewId'] != args.get('previewId') or any(
            checked[key] != expected[key] for key in ('bundle', 'fingerprint', 'selection')):
        raise ValueError('The bundle selection changed. Preview it again before applying.')
    if not checked['modelCompatible'] and not args.get('resetModel'):
        raise ValueError('The selected model is unavailable in this bundle. Choose Use bundle model before switching.')
    await worker.controls.checkpoint()
    old_config = copy.deepcopy(worker.start_config)
    journal = BundleTransaction(worker.home, worker.workspace, worker.runtime.session_id)
    journal.begin()
    worker.remounting = True
    async def dispose():
        ownership = getattr(worker, "ownership", None)
        if ownership and ownership.registration:
            await ownership.registration.close()
            ownership.registration = None
        if worker.execution and not worker.execution.done():
            worker.execution.cancel()
            await asyncio.gather(worker.execution, return_exceptions=True)
        if worker.controls: await worker.controls.close()
        if worker.session: await worker.session.cleanup()
        worker.session = worker.controls = worker.naming = worker.execution = None
    try:
        await dispose()
        journal.select(args['bundle'], reset_model=args.get('resetModel', False))
        new_config = {**old_config, 'bundle': args['bundle']}
        new_config.pop('selection', None)  # The current pin is in control-state.json.
        await asyncio.wait_for(worker.start(new_config, raise_errors=True, recover_bundle=False), 600)
        journal.commit()
        worker.bundle_preview = None
        return {'bundle': args['bundle'], 'configuration': worker.controls.configuration(),
                'providers': await worker.controls.perform('configuration.providers'), 'historyPreserved': True}
    except BaseException:
        try:
            await dispose()
        finally:
            journal.restore()
        await worker.start(old_config, raise_errors=True, recover_bundle=False)
        raise
    finally:
        worker.remounting = False
