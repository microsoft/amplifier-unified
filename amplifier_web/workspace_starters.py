"""Reusable workspace starter definitions; templates are data, never executable hooks."""
from __future__ import annotations

import copy
import json
import re
import uuid
from urllib.parse import urlsplit

from filelock import FileLock
from amplifier_worktrees.git import atomic
from .host.config import PRECONFIGURED_BUNDLES
from .bundle_selection import catalog_entry

DEVELOPMENT_INSTRUCTIONS = """# Development workspace

This workspace is a lasting home for related conversations and files.
Keep persistent projects and temporary task working areas distinct. Never delete
or publish the workspace container as though it were the project's source.

Before changing a repository, read its AGENTS.md, CONTRIBUTING.md, README.md and
relevant local conventions. More specific subtree guidance applies within it.
Preserve unrelated edits, branches and remotes. Use the repository or task working
directory explicitly; do not silently reuse another checkout elsewhere.

Keep unfinished plans and working notes with their own task, not in one shared
workspace scratch file. Share decisions deliberately with their source.
Record external resources, their owners and cleanup evidence using the host's
workspace resource actions. Recording a resource does not authorize teardown.
Verify results at the user-visible boundary before claiming completion.
"""

BUILTINS = [
    {'id': 'blank', 'name': 'Blank', 'description': 'An empty folder. Use your existing defaults.',
     'instructions': '', 'bundle': '', 'repositories': [], 'trackResources': False},
    {'id': 'development', 'name': 'Development', 'description': 'Repository guidance and resource tracking; add your own repositories.',
     'instructions': DEVELOPMENT_INSTRUCTIONS, 'bundle': '', 'repositories': [], 'trackResources': True},
    {'id': 'amplifier-development', 'name': 'Amplifier development',
     'description': 'Amplifier, Core and Foundation with the Amplifier development bundle.',
     'instructions': DEVELOPMENT_INSTRUCTIONS, 'bundle': 'anchors-amp-dev', 'trackResources': True,
     'repositories': [{'url': f'https://github.com/microsoft/{name}.git', 'directory': name, 'ref': ''}
                      for name in ('amplifier', 'amplifier-core', 'amplifier-foundation')]},
]
FIELDS = {'name', 'description', 'instructions', 'bundle', 'repositories', 'trackResources'}


def repository(value):
    if not isinstance(value, dict) or set(value) - {'url', 'directory', 'ref'}:
        raise ValueError('A repository needs a source URL and a child folder name.')
    if any(not isinstance(value.get(key, ''), str) for key in ('url', 'directory', 'ref')):
        raise ValueError('Repository source, folder and ref must be text.')
    url = value.get('url', '').strip()
    parts = urlsplit(url)
    https = parts.scheme == 'https' and parts.hostname and not parts.username and not parts.password
    ssh = parts.scheme == 'ssh' and parts.hostname and not parts.password
    scp = bool(re.fullmatch(r'[\w.-]+@[\w.-]+:[\w./-]+', url))
    if not (https or ssh or scp) or parts.query or parts.fragment or any(c.isspace() for c in url) or len(url) > 2000:
        raise ValueError('Use an HTTPS or SSH Git URL without embedded credentials, query or fragment.')
    directory = value.get('directory', '').strip()
    if not directory:
        directory = url.rsplit('/', 1)[-1].removesuffix('.git')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,99}', directory) or directory.lower() in {'con', 'prn', 'aux', 'nul'}:
        raise ValueError('Repository folders must be simple child names (letters, numbers, hyphens or underscores).')
    ref = value.get('ref', '').strip()
    if ref and (len(ref) > 200 or not re.fullmatch(r'[\w][\w./-]*', ref) or '..' in ref or ref.endswith(('/', '.', '.lock')) or '//' in ref):
        raise ValueError('Use a branch or tag name, or leave it blank for the remote default branch.')
    return {'url': url, 'directory': directory, 'ref': ref}


def definition(value, bundle_names=None):
    if not isinstance(value, dict) or set(value) - FIELDS:
        raise ValueError('Unknown starter field.')
    limits = {'name': 200, 'description': 1000, 'instructions': 16000, 'bundle': 2000}
    result = {}
    for key, limit in limits.items():
        item = value.get(key, '')
        if not isinstance(item, str) or len(item) > limit or '\0' in item:
            raise ValueError(f'Invalid starter {key}.')
        result[key] = item.strip()
    if not result['name']:
        raise ValueError('Name this workspace starter.')
    if result['bundle'] and result['bundle'] not in (bundle_names if bundle_names is not None else PRECONFIGURED_BUNDLES):
        raise ValueError('Choose a configured standalone bundle, or inherit the existing default.')
    repos = value.get('repositories', [])
    if not isinstance(repos, list) or len(repos) > 20:
        raise ValueError('A starter supports up to 20 repositories.')
    result['repositories'] = [repository(row) for row in repos]
    names = [row['directory'].casefold() for row in result['repositories']]
    if len(names) != len(set(names)):
        raise ValueError('Each repository needs a distinct folder name.')
    tracking = value.get('trackResources', False)
    if type(tracking) is not bool:
        raise ValueError('Resource tracking must be true or false.')
    result['trackResources'] = tracking
    return result


class StarterCatalog:
    def __init__(self, home, bundles=None, sources=None):
        self.path = home / 'workspace-starters.json'
        self.bundles = copy.deepcopy(bundles if bundles is not None else [catalog_entry(name) for name in PRECONFIGURED_BUNDLES])
        self.bundle_names = {row['value'] for row in self.bundles}
        self.sources = dict(sources or {})

    def _read(self):
        if not self.path.exists():
            return {'revision': 0, 'items': [], 'commands': {}}
        try:
            value = json.loads(self.path.read_text())
            if not isinstance(value['items'], list) or type(value['revision']) is not int:
                raise ValueError()
            return value
        except (ValueError, KeyError, TypeError):
            raise ValueError('The workspace starter library is unreadable. Preserve it and repair it before saving.') from None

    def listing(self):
        value = self._read()
        builtins = [{**copy.deepcopy(row), 'builtIn': True, 'revision': 2 if row['id'] == 'amplifier-development' else 1} for row in BUILTINS]
        return {'revision': value['revision'], 'items': builtins + copy.deepcopy(value['items']),
                'bundles': copy.deepcopy(self.bundles)}

    def snapshot(self, identity='blank'):
        row = next((row for row in self.listing()['items'] if row['id'] == identity), None)
        if row is None:
            raise ValueError('This starter is unavailable. Choose another starter.')
        definition({key: row[key] for key in FIELDS}, self.bundle_names)
        # A project/private standalone registration must remain resolvable from
        # the new workspace too. Retain only its chosen source, never providers.
        if row['bundle'] and row['bundle'] in self.sources:
            row['bundleSource'] = self.sources[row['bundle']]
        return row

    def command(self, action, args, command_id):
        if action == 'workspace.starters.list':
            return self.listing()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(str(self.path) + '.lock'):
            value = self._read()
            fingerprint = json.dumps([action, args], sort_keys=True)
            prior = value.get('commands', {}).get(command_id) if command_id else None
            if prior:
                if prior['fingerprint'] != fingerprint:
                    raise ValueError('This request already saved a different starter change.')
                return prior['result']
            identity = args.get('id')
            old = next((row for row in value['items'] if row['id'] == identity), None)
            if action == 'workspace.starters.duplicate':
                source = self.snapshot(args['id'])
                fields = {key: source[key] for key in FIELDS}
                fields['name'] = args.get('name') or source['name'] + ' copy'
                row = {**definition(fields, self.bundle_names), 'id': str(uuid.uuid4()), 'builtIn': False, 'revision': 1}
                value['items'].append(row)
                result = row
            elif action == 'workspace.starters.save':
                if identity and not old:
                    raise ValueError('Built-in starters cannot be edited. Duplicate one to customize it.')
                if old and args.get('expectedRevision') != old['revision']:
                    raise ValueError('This starter changed. Reload it before saving.')
                row = {**definition(args['starter'], self.bundle_names), 'id': identity or str(uuid.uuid4()), 'builtIn': False,
                       'revision': old['revision'] + 1 if old else 1}
                value['items'] = [row if item['id'] == identity else item for item in value['items']] if old else value['items'] + [row]
                result = row
            elif action == 'workspace.starters.remove':
                if not old:
                    raise ValueError('Only custom starters can be deleted.')
                if args['expectedRevision'] != old['revision']:
                    raise ValueError('This starter changed. Reload it before deleting.')
                value['items'] = [item for item in value['items'] if item['id'] != identity]
                result = {'id': identity, 'removed': True}
            else:
                raise ValueError('Unknown starter action.')
            if len(value['items']) > 100:
                raise ValueError('The starter library supports up to 100 custom starters.')
            value['revision'] += 1
            if command_id:
                commands = value.setdefault('commands', {})
                commands[command_id] = {'fingerprint': fingerprint, 'result': result}
                value['commands'] = dict(list(commands.items())[-200:])
            atomic(self.path, value)
            return copy.deepcopy(result)


def configured_catalog(service):
    from .host.config import read_config
    from .bundles import offered_catalog
    config = read_config(service.state['settings']['workspace'], home=service.data_dir)
    return StarterCatalog(service.data_dir, offered_catalog(config), {
        name: config.resolve_source(config.registrations[name]) or config.registrations[name]
        for name in config.settings.get('bundle', {}).get('added', {})
        if name in config.registrations
    })
