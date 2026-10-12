"""Media-only canonical identity facts; never hydrate transcript bodies."""
import copy


ERROR = 'Choose an existing saved assistant message in this conversation.'
FIELDS = ('id', 'nativeProject', 'nativeIdentity', 'runtimeSessionId',
          'historyManaged', 'nativeRevision', 'executionRevision', 'workspace',
          'workingDirectory', 'sessionKind', 'parentId', 'forkTranscript', 'collaborationGeneration')


def snapshot(session, message_id):
    if not isinstance(message_id, str) or not 0 < len(message_id) <= 200:
        raise ValueError(ERROR)
    # ColdRecord.get() and _session(hydrate=True) can read entire display
    # resources. Media must never invoke those virtual getters under its lock.
    rows = [row for row in dict.get(session, 'messages', []) if row.get('id') == message_id]
    if len(rows) > 1 or rows and (rows[0].get('role') != 'assistant'
                                or rows[0].get('observation') or rows[0].get('observation_id')):
        raise ValueError(ERROR)
    aliases = [row.get('id') for row in dict.get(session, 'messages', [])
               if rows and row.get('id') != message_id
               and (type(rows[0].get('nativeIndex')) is int and row.get('nativeIndex') == rows[0]['nativeIndex']
                    or rows[0].get('nativeMessageId') and row.get('nativeMessageId') == rows[0]['nativeMessageId'])]
    if aliases:
        raise ValueError(ERROR)
    return {**{key: copy.deepcopy(dict.get(session, key)) for key in FIELDS},
            'coldMessages': copy.deepcopy(dict.get(session, '_coldFields', {}).get('messages')),
            'rows': [{key: copy.deepcopy(row.get(key)) for key in
                      ('id', 'role', 'nativeIndex', 'nativeMessageId', 'source', 'text')}
                     for row in rows]}


def canonical_required(value):
    rows = value['rows']
    return bool(value.get('nativeProject') and
                (value.get('historyManaged') or not rows or rows[0].get('source') == 'native'
                 or rows[0].get('nativeMessageId') or rows[0].get('nativeIndex') is not None))


def resolve(value, message_id):
    """Worker-only IO. Reuse the navigator's cached, full-text-derived IDs.

    A warm old-window lookup reads no bodies. A cold projection streams compact
    facts off-thread through Foundation's revision-checked index, not a full
    read_transcript or a durable media identity cache.
    """
    if not canonical_required(value):
        if not value['rows']:
            raise ValueError(ERROR)
        return {'snapshot': value, 'fact': (message_id, 'assistant'), 'revision': None}
    from .automatic_history import revision, display_identity
    from .conversation_navigation import source
    before = revision(value)
    facts, _ = source({**value, 'historyManaged': True})
    identity = message_id
    if value['rows']:
        row = value['rows'][0]
        if type(row.get('nativeIndex')) is int:
            identity = display_identity(value, row['nativeIndex'], row['role'], row.get('text') or '')
            if row.get('nativeMessageId') not in (None, identity):
                raise ValueError(ERROR)
            if value.get('historyManaged') and row.get('source') == 'native' and row['id'] != identity:
                raise ValueError(ERROR)
        elif row.get('nativeMessageId'):
            raise ValueError(ERROR)
    matches = [fact for fact in facts if fact[0] == identity]
    if (before is None or revision(value) != before or len(matches) != 1
            or matches[0][1] != 'assistant' or not matches[0][2]
            or value['rows'] and type(value['rows'][0].get('nativeIndex')) is int
            and matches[0][3] != value['rows'][0]['nativeIndex']):
        raise ValueError(ERROR)
    return {'snapshot': value, 'fact': matches[0], 'revision': before}


def validate(session, message_id, proof):
    if proof['snapshot'] != snapshot(session, message_id):
        raise ValueError('The saved assistant identity changed during media access.')
    return {'id': message_id, 'role': 'assistant'}

