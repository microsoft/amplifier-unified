"""Opt-in browser transport. Authoritative app/agent snapshots stay unchanged."""
import hashlib
import marshal


def changes(previous, current, path=()):
    """Replace changed leaves, preserving large unchanged catalogs and assets.

    Arrays are patched by index only when their shape is unchanged. Inserts,
    removals and reordering therefore cannot leave dangling indexed records.
    Each stream owns its baseline; reconnect always starts with a full snapshot.
    """
    if previous == current:
        return []
    if isinstance(previous, dict) and isinstance(current, dict):
        result = [{'path': [*path, key], 'remove': True} for key in previous.keys() - current.keys()]
        for key, value in current.items():
            if key not in previous:
                result.append({'path': [*path, key], 'value': value})
            else:
                result.extend(changes(previous[key], value, (*path, key)))
        return result
    if (isinstance(previous, list) and isinstance(current, list) and len(previous) == len(current)
            and all(not isinstance(old, dict) or not isinstance(new, dict)
                    or old.get('id') == new.get('id') for old, new in zip(previous, current))):
        result = []
        for index, value in enumerate(current):
            result.extend(changes(previous[index], value, (*path, index)))
        return result
    return [{'path': list(path), 'value': current}]


def delta(previous, current):
    return {'baseRevision': previous['revision'], 'revision': current['revision'],
            'changes': changes(previous, current)}


def _message_page(snapshot):
    sessions = snapshot['sessions']
    if len(sessions) != 1:
        return None
    row = sessions[0]
    window = row.get('messageWindow') or {}
    if (row.get('id') != snapshot.get('selectedSessionId') or row.get('historyLoading')
            or row.get('historyError') or not row.get('messages')
            or not window.get('revision') or not window.get('windowKey')):
        return None
    return {'sessionId': row['id'], 'revision': window['revision'], 'windowKey': window['windowKey']}


def _row_digest(snapshot):
    # Window revisions identify canonical source. Reactions, permissions and
    # other display annotations can change independently of that source.
    # A process-local, typed encoding avoids JSON string escaping on the hot
    # path. Format 2 has no reference-sharing dependency. Never load or export
    # these bytes. Unrecognized JSON-compatible subclasses use normal delivery.
    try:
        rows = marshal.dumps(snapshot['sessions'][0]['messages'], 2)
    except (TypeError, ValueError, OverflowError):
        return None
    return hashlib.sha256(rows).digest()


class WarmPageDelta:
    """One departed-page proof per connection. Never retain departed row bodies."""

    def __init__(self):
        self.departed = None

    def build(self, previous, current):
        payload = delta(previous, current)
        if previous.get('selectedSessionId') == current.get('selectedSessionId'):
            return payload

        retained = self.departed
        self.departed = None
        leaving = _message_page(previous)
        if leaving:
            digest = _row_digest(previous)
            if digest is not None:
                self.departed = (leaving, previous['revision'], digest)

        replacement = next((change for change in payload['changes'] if change['path'] == ['sessions']), None)
        if (retained and replacement and _message_page(current) == retained[0]
                and _row_digest(current) == retained[2]):
            row = current['sessions'][0]
            replacement['value'] = [{key: value for key, value in row.items() if key != 'messages'}]
            payload['reuseMessages'] = {**retained[0], 'fromRevision': retained[1]}
        return payload
