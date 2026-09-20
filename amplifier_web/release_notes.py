"""Bounded, data-only release history shared by Updates and the publisher.

This module uses only the standard library so release.py can load it without
importing the application or installing its runtime dependencies.
"""
import copy
import json
from functools import lru_cache
from pathlib import Path
import re

MAX_BYTES = 256_000
MAX_RELEASES = 100
PATH = Path(__file__).with_name('release-notes.json')


def version(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d+\.\d+\.\d+', value):
        raise ValueError('Release notes require stable versions')
    return tuple(map(int, value.split('.')))


def parse(raw, expected=None):
    if len(raw.encode('utf-8')) > MAX_BYTES:
        raise ValueError('Release notes are too large')
    data = json.loads(raw)
    if not isinstance(data, dict) or data.get('schemaVersion') != 1:
        raise ValueError('Unsupported release notes format')
    entries = data.get('releases')
    if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_RELEASES:
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
        changes, notices = entry.get('changes'), entry.get('notices', [])
        if not isinstance(changes, list) or not 1 <= len(changes) <= 30 or not isinstance(notices, list) or len(notices) > 5:
            raise ValueError('Invalid release changes or notices')
        clean = {'version': number, 'title': text(entry.get('title'), 160),
                 'changes': [text(change, 1500) for change in changes], 'notices': []}
        ids = set()
        for notice in notices:
            if not isinstance(notice, dict) or not isinstance(notice.get('id'), str) or not re.fullmatch(r'[a-z0-9-]{1,80}', notice['id']):
                raise ValueError('Invalid release notice identity')
            if notice['id'] in ids:
                raise ValueError('Duplicate release notice')
            ids.add(notice['id'])
            clean['notices'].append({'id': notice['id'], 'title': text(notice.get('title'), 160),
                                     'detail': text(notice.get('detail'), 2000), 'action': text(notice.get('action'), 1500)})
        result.append(clean)
    if expected and (expected not in seen or any(version(row['version']) > version(expected) for row in result)):
        raise ValueError('Release notes must include the published version and no future versions')
    return sorted(result, key=lambda row: version(row['version']), reverse=True)


@lru_cache(maxsize=1)
def bundled():
    try:
        return parse(PATH.read_text())
    except (OSError, ValueError):
        # A missing history must not prevent startup or updating a damaged app.
        return []


def history(cached=(), latest=None):
    """Installed entries win over stale cached text; retain fetched future notes."""
    from . import __version__
    try:
        saved = parse(json.dumps({'schemaVersion': 1, 'releases': cached})) if cached else []
    except (ValueError, TypeError):
        saved = []
    ceiling = max(version(__version__), version(latest.removeprefix('v'))) if latest else max([version(__version__), *[version(row['version']) for row in saved]])
    entries = {row['version']: row for row in [*saved, *bundled()] if version(row['version']) <= ceiling}
    return copy.deepcopy(sorted(entries.values(), key=lambda row: version(row['version']), reverse=True)[:MAX_RELEASES])


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
