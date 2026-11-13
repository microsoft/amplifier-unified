"""External ACP launch settings in the existing private server configuration."""
from __future__ import annotations

import copy
import hashlib
import json
import uuid

from .acp_participant import endpoint
from .deployment import load_server_config, save_server_config
from .errors import AppError


def definitions(schema, string):
    identity = {**string(100), 'minLength': 1}
    agent = schema({
        'id': identity, 'label': {**string(200), 'minLength': 1},
        'command': {**string(4000), 'minLength': 1},
        'args': {'type': 'array', 'maxItems': 256, 'items': string(4000)},
        'cwd': string(4000), 'sessionCwd': string(4000),
    }, ['label', 'command'])
    return {
        'externalAgents.list': ('List saved external agent names for chat creation; no launch arguments or credentials.', schema({}, [])),
        'externalAgents.get': ('Read one private external launch configuration. Agents use existing global connections authority.', schema({'id': identity}, ['id'])),
        'externalAgents.save': ('Save an external launch configuration for immediate use. Omit id to add; existing entries require their expectedRevision. Does not connect, install or rebind existing chats.', schema({'agent': agent, 'expectedRevision': string(64)}, ['agent'])),
        'externalAgents.remove': ('Remove an external launch configuration at its expectedRevision. Keeps chats, history and already accepted work.', schema({'id': identity, 'expectedRevision': string(64)}, ['id', 'expectedRevision'])),
    }


def _rows(config):
    rows = config.get('externalAgents', [])
    if not isinstance(rows, list):
        raise AppError('externalAgents in the server configuration must be a list.')
    return rows


def _config(service):
    config = getattr(service, 'server_config', None)
    return config if config is not None else load_server_config(service.data_dir)


def revision(row):
    return hashlib.sha256(json.dumps(row, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def catalog(service):
    return {'agents': [{'id': row['id'], 'label': row['label'], 'revision': revision(row)}
                       for row in _rows(_config(service))
                       if isinstance(row, dict) and isinstance(row.get('id'), str)
                       and isinstance(row.get('label'), str)]}


def read(service, identity):
    row = next((row for row in _rows(_config(service))
                if isinstance(row, dict) and row.get('id') == identity), None)
    if row is None:
        raise AppError('This external agent is not configured on this host.', 404)
    return {'revision': revision(row), 'agent': copy.deepcopy({key: row[key] for key in
            ('id', 'label', 'command', 'args', 'cwd', 'sessionCwd') if key in row})}


def mutate(service, action, args, command_id=None):
    """Called under the host action lock; save before changing the live selection."""
    selected = None
    generated = False
    if action == 'externalAgents.save':
        selected = copy.deepcopy(args['agent'])
        generated = 'id' not in selected
        selected.setdefault('id', str(uuid.uuid5(uuid.NAMESPACE_URL,
            'amplifier-external-agent:' + command_id) if command_id else uuid.uuid4()))
        try:
            selected = endpoint({'externalAgents': [selected]}, selected['id'])
        except ValueError as exc:
            raise AppError(str(exc)) from None
        identity = selected['id']
    else:
        identity = args['id']
    # Read the canonical file now, retaining unrelated settings changed since
    # this host started. Its existing atomic/private writer owns publication.
    config = load_server_config(service.data_dir)
    rows = list(_rows(config))
    index = next((index for index, row in enumerate(rows)
                  if isinstance(row, dict) and row.get('id') == identity), None)
    # The private file can commit before the ordinary command receipt. Recover
    # that exact creation without publishing a second row or replaying a write.
    recovered = bool(generated and command_id and index is not None
                     and args.get('expectedRevision') is None and rows[index] == selected)
    if index is not None and not recovered and args.get('expectedRevision') != revision(rows[index]):
        raise AppError('This external agent changed. Reload its settings before saving or removing it.', 409)
    if index is None and args.get('expectedRevision') is not None:
        raise AppError('This external agent was removed. Reload its settings before saving it.', 409)
    if action == 'externalAgents.remove':
        if index is None:
            raise AppError('This external agent is not configured on this host.', 404)
        rows.pop(index)
    elif recovered:
        pass
    elif index is None:
        rows.append(selected)
    else:
        previous = rows[index]
        # An editor's empty argument list is equivalent to omitted arguments.
        # Keep the existing representation so a name-only edit retains identity.
        if 'args' not in previous and selected.get('args') == []:
            selected.pop('args')
        unknown = {key: value for key, value in previous.items()
                   if key not in {'id', 'label', 'command', 'args', 'cwd', 'sessionCwd'}}
        rows[index] = {**unknown, **selected}
    try:
        saved = config if recovered else save_server_config(service.data_dir, {**config, 'externalAgents': rows})
    except OSError:
        raise AppError('The host could not save external agent settings. Check its configuration storage.', 500) from None
    if getattr(service, 'server_config', None) is None:
        service.server_config = saved
    else:
        service.server_config['externalAgents'] = saved['externalAgents']
    return {**catalog(service), **({'savedId': identity} if selected is not None else {}),
            **({'recovered': True} if recovered else {})}
