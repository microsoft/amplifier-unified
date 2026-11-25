"""Bounded, data-only release history shared by Updates and the publisher.

This module uses only the standard library so release.py can load it without
importing the application or installing its runtime dependencies.
"""
import copy
from datetime import date
import json
from functools import lru_cache
from pathlib import Path
import re

# Hard bounds do not grow with the archive. Guidance never ages out of this view.
MAX_BYTES = 512_000
MAX_RELEASES = 512
RECENT_ORDINARY = 256
PATH = Path(__file__).with_name('release-notes.json')


class HistoryOverflow(ValueError):
    """History cannot fit safely; do not mistake lost guidance for no guidance."""


def version(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d+\.\d+\.\d+', value):
        raise ValueError('Release notes require stable versions')
    return tuple(map(int, value.split('.')))


def parse(raw, expected=None):
    if len(raw.encode('utf-8')) > MAX_BYTES:
        raise HistoryOverflow('Release notes are too large')
    data = json.loads(raw)
    if not isinstance(data, dict) or data.get('schemaVersion') != 1:
        raise ValueError('Unsupported release notes format')
    entries = data.get('releases')
    if isinstance(entries, list) and len(entries) > MAX_RELEASES:
        raise HistoryOverflow('Release notes require a bounded release history')
    return _validate(entries, expected)


def _validate(entries, expected=None):
    """Validate every row before selection, including rows that may age out."""
    if not isinstance(entries, list) or not entries:
        raise ValueError('Release notes require a bounded release history')

    def text(value, limit):
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            raise ValueError('Invalid release notes text')
        return value

    result, seen = [], set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError('Invalid release entry')
        number = entry.get('version')
        version(number)
        if number in seen:
            raise ValueError('Duplicate release version')
        seen.add(number)
        if 'date' in entry:
            value = entry['date']
            if not isinstance(value, str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', value):
                raise ValueError('Release date must be YYYY-MM-DD')
            date.fromisoformat(value)  # Reject impossible calendar dates too.
        changes, notices = entry.get('changes'), entry.get('notices', [])
        if not isinstance(changes, list) or not 1 <= len(changes) <= 30 or not isinstance(notices, list) or len(notices) > 5:
            raise ValueError('Invalid release changes or notices')
        clean = {**entry, 'version': number, 'title': text(entry.get('title'), 160),
                 'changes': [text(change, 1500) for change in changes], 'notices': []}
        ids = set()
        for notice in notices:
            if not isinstance(notice, dict) or not isinstance(notice.get('id'), str) or not re.fullmatch(r'[a-z0-9-]{1,80}', notice['id']):
                raise ValueError('Invalid release notice identity')
            if notice['id'] in ids:
                raise ValueError('Duplicate release notice')
            ids.add(notice['id'])
            item = {**notice, 'id': notice['id'], 'title': text(notice.get('title'), 160),
                    'detail': text(notice.get('detail'), 2000), 'action': text(notice.get('action'), 1500)}
            # Explicit title aliases preserve reviews of editorial corrections.
            # Detail or action changes still require a new acknowledgement.
            previous = notice.get('previousTitles', [])
            if not isinstance(previous, list) or len(previous) > 5:
                raise ValueError('Invalid previous release notice titles')
            if previous:
                item['previousTitles'] = [text(title, 160) for title in previous]
            clean['notices'].append(item)
        result.append(clean)
    if expected and (expected not in seen or any(version(row['version']) > version(expected) for row in result)):
        raise ValueError('Release notes must include the published version and no future versions')
    return sorted(result, key=lambda row: version(row['version']), reverse=True)


def serialize(entries):
    """The package format; byte budgeting includes this exact UTF-8 wrapper."""
    return '{\n  "schemaVersion": 1,\n  "releases": [\n    ' + ',\n    '.join(
        json.dumps(row, ensure_ascii=False, separators=(',', ':')) for row in entries) + '\n  ]\n}\n'


def retain(entries, expected=None):
    """Keep all guidance and newest ordinary rows, then fit the unchanged bounds.

    Inputs are already source-merged by the caller. Validate before pruning, but
    select whole original records (including aliases and other authored fields).
    Protected guidance alone must fit; a newly prepared current entry must fit
    beside it too. No acknowledgements or archived releases are changed.
    """
    if entries == []:
        if expected:
            raise ValueError('Release notes must include the published version')
        return []
    _validate(entries, expected)
    ordered = sorted(entries, key=lambda row: version(row['version']), reverse=True)
    protected = [row for row in ordered if row.get('notices')]
    ordinary = [row for row in ordered if not row.get('notices')][:RECENT_ORDINARY]
    # Encode each record once, not the whole history again for every removal.
    sizes = {row['version']: len(json.dumps(row, ensure_ascii=False,
             separators=(',', ':')).encode('utf-8')) for row in [*protected, *ordinary]}
    separator_bytes = len(',\n    '.encode('utf-8'))
    def byte_size(rows):
        return (len(serialize([]).encode('utf-8')) + sum(sizes[row['version']] for row in rows)
                + separator_bytes * max(0, len(rows) - 1))
    if len(protected) > MAX_RELEASES or byte_size(protected) > MAX_BYTES:
        raise HistoryOverflow('Release history cannot fit all protected high-impact notices')
    count = len(protected) + len(ordinary)
    size = byte_size([*protected, *ordinary])
    while ordinary and (count > MAX_RELEASES or size > MAX_BYTES):
        row = ordinary.pop()  # Always the oldest ordinary entry, never a notice.
        size -= sizes[row['version']] + (separator_bytes if count > 1 else 0)
        count -= 1
    kept = sorted([*protected, *ordinary], key=lambda row: version(row['version']), reverse=True)
    if expected and not any(row['version'] == expected for row in kept):
        raise HistoryOverflow('Release history cannot fit the current release beside protected notices')
    return copy.deepcopy(kept)


@lru_cache(maxsize=1)
def bundled():
    try:
        # Keep the complete validated source until history() resolves version
        # conflicts. Pruning here could revive stale cached notice wording.
        return parse(PATH.read_text(encoding='utf-8'))
    except HistoryOverflow:
        raise  # A capacity failure must not erase protected guidance as [].
    except (OSError, ValueError):
        # A missing history must not prevent startup or updating a damaged app.
        return []


def history(cached=(), latest=None):
    """Installed entries win over stale cached text; retain fetched future notes."""
    from . import __version__
    try:
        saved = parse(json.dumps({'schemaVersion': 1, 'releases': cached},
                                 ensure_ascii=False, separators=(',', ':'))) if cached else []
    except HistoryOverflow:
        raise
    except (ValueError, TypeError):
        saved = []
    ceiling = max(version(__version__), version(latest.removeprefix('v'))) if latest else max([version(__version__), *[version(row['version']) for row in saved]])
    entries = {row['version']: row for row in [*saved, *bundled()] if version(row['version']) <= ceiling}
    return retain(list(entries.values()))


def notice_id(release, notice):
    return 'release-notice:' + release['version'] + ':' + notice['id']


def markdown(entries, number):
    entry = next(row for row in entries if row['version'] == number)
    lines = ['## ' + entry['title'], '']
    if entry['notices']:
        lines += ['### High-impact changes', '']
        for notice in entry['notices']:
            lines += ['**' + notice['title'] + '**', '', notice['detail'], '', '**What to do:** ' + notice['action'], '']
    lines += ['### Changes', '', *['- ' + change for change in entry['changes']], '']
    return '\n'.join(lines)
