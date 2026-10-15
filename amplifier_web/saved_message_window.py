"""Bounded suffix display over an exact immutable saved-message resource.

This proves only the requested window and its immutable resource/file ownership;
it does not claim to rehash or validate an unrequested array prefix.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import stat

from .transcript_window import _bounded_json, _OBJECT_LIMIT

MAX_BYTES = 8 * 1024 * 1024
MAX_RECORD = 4 * 1024 * 1024


def _signature(info):
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns]


def read_saved_window(source, signer, *, session_id, fence, before=None, limit=100):
    from .resource_files import _blob_index
    blob = _blob_index(source)
    if blob:
        path = source.directory / (source.identity + '.json')
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
        stream = os.fdopen(fd, 'rb')
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode):
            stream.close()
            raise ValueError('Saved message source must be a regular file.')
        signature = _signature(info)
        size = info.st_size
    else:
        if len(source.indexed) > MAX_BYTES:
            raise ValueError('Inline saved message source exceeds the window budget.')
        raw = source.indexed.encode()
        if len(raw) > MAX_BYTES or hashlib.sha256(raw).hexdigest() != source.identity:
            raise ValueError('Invalid inline saved message source.')
        stream = io.BytesIO(raw)
        signature, size = ['inline', source.identity], len(raw)
    scope = [session_id, source.identity, fence, signature]
    revision = hashlib.sha256(json.dumps(scope, separators=(',', ':')).encode()).hexdigest()
    with stream:
        end = size
        state = {'depth': 0, 'quoted': False, 'quote': False, 'slashes': 0, 'skip': False}
        if before is not None:
            try:
                cursor = signer._parse(before)
            except Exception:
                raise ValueError('Invalid saved message cursor.') from None
            if (cursor.get('kind') != 'saved-resource' or cursor.get('revision') != revision
                    or type(cursor.get('end')) is not int or not 0 <= cursor['end'] <= size):
                raise ValueError('Saved message resource changed. Refresh its window.')
            end, state = cursor['end'], cursor['state']
        start = max(0, end - (MAX_BYTES - 1))
        stream.seek(0)
        if stream.read(1) != b'[':
            raise ValueError('Saved messages must be an array.')
        stream.seek(start)
        raw = stream.read(end - start)
        if len(raw) != end - start:
            raise ValueError('Saved message source changed during read.')
        items, gaps = [], []
        position = len(raw) - 1
        record_end = end
        if before is None:
            while position >= 0 and raw[position] in b' \t\r\n':
                position -= 1
            if position < 0 or raw[position] != 93:
                raise ValueError('Saved message array has no closing boundary.')
            position -= 1
            record_end = start + position + 1
        next_end, next_state = end, dict(state)
        completed = 0
        while position >= 0:
            byte = raw[position]
            absolute = start + position
            if state['quote']:
                if byte == 92:
                    state['slashes'] ^= 1
                    position -= 1
                    continue
                if not state['slashes']:
                    state['quoted'] = not state['quoted']
                state['quote'], state['slashes'] = False, 0
            if byte == 34:
                state['quote'] = True
            elif not state['quoted']:
                if state['depth'] == 0 and byte in b' \t\r\n,':
                    record_end = absolute
                elif state['depth'] == 0 and byte == 91:
                    next_end = 0
                    break
                elif byte in (93, 125):
                    state['depth'] += 1
                elif byte in (91, 123):
                    state['depth'] -= 1
                    if state['depth'] < 0:
                        raise ValueError('Invalid saved message array boundary.')
                    if state['depth'] == 0:
                        if byte != 123:
                            raise ValueError('Saved message rows must be objects.')
                        if state['skip'] or record_end - absolute > MAX_RECORD:
                            gaps.append({'start': absolute, 'end': record_end, 'reason': 'record_limit'})
                        else:
                            value = _bounded_json(raw[position:record_end - start])
                            if value is _OBJECT_LIMIT:
                                gaps.append({'start': absolute, 'end': record_end, 'reason': 'object_limit'})
                            elif (not isinstance(value, dict) or not isinstance(value.get('id'), str)
                                  or not 0 < len(value['id']) <= 200):
                                raise ValueError('Invalid saved message identity.')
                            else:
                                items.append(value)
                        completed += 1
                        state = {'depth': 0, 'quoted': False, 'quote': False, 'slashes': 0, 'skip': False}
                        next_end, next_state = absolute, dict(state)
                        record_end = absolute
                        if len(items) >= limit or completed >= 512:
                            break
            position -= 1
        if position < 0:
            if start == 0:
                next_end = 0
            elif completed and end - next_end < MAX_BYTES - 1:
                # Retry the next complete record with a fresh byte budget.
                pass
            else:
                gaps.append({'start': start, 'end': record_end, 'reason': 'read_limit'})
                state['skip'] = True
                next_end, next_state = start, dict(state)
        if blob and (_signature(os.fstat(stream.fileno())) != signature or _signature(path.stat()) != signature):
            raise ValueError('Saved message resource changed during read.')
    token = signer._token({'kind': 'saved-resource', 'revision': revision,
                          'end': next_end, 'state': next_state}) if next_end else None
    items.reverse()
    gaps.reverse()
    return {'items': items, 'mode': 'cursor', 'before': token, 'hasEarlier': bool(next_end),
            'total': None, 'offset': None, 'scope': 'recent', 'revision': revision,
            'windowKey': hashlib.sha256(json.dumps([revision, end, next_end]).encode()).hexdigest(),
            'coverage': {'kind': 'saved-web', 'validation': 'window', 'historyComplete': False,
                         'scannedBytes': len(raw) + 1, 'rangeStart': next_end, 'rangeEnd': end},
            'unavailableRanges': gaps}


def resident_rows(rows, end, limit):
    """Bound an already-resident page before copying or encoding any body."""
    accepted, gaps = [], []
    remaining, nodes, examined = MAX_BYTES, 100_000, 0
    position = end
    while position and examined < limit:
        index = position - 1
        value = rows[index]
        cost, invalid = 0, None
        if type(value) is not dict or not isinstance(value.get('id'), str):
            position = index
            examined += 1
            gaps.append({'index': index, 'reason': 'object_limit'})
            continue
        # Iterators avoid copying a large container merely to inspect its size.
        stack = [(iter((value,)), 0)]
        while stack:
            iterator, depth = stack[-1]
            try:
                item = next(iterator)
            except StopIteration:
                stack.pop()
                continue
            nodes -= 1
            cost += 2  # Container comma/colon delimiters.
            if nodes < 0:
                invalid = 'object_limit'
                break
            if depth > 64:
                invalid = 'object_limit'
                break
            if isinstance(item, str):
                cost += 6 * len(item) + 2  # UTF-8 and JSON control escaping; no encoding.
            elif type(item) is int:
                cost += max(64, item.bit_length())
            elif item is None or type(item) in (bool, float):
                cost += 64
            elif type(item) is dict:
                cost += 2
                stack.append((iter(item.keys()), depth + 1))
                stack.append((iter(item.values()), depth + 1))
            elif type(item) in (list, tuple):
                cost += 2
                stack.append((iter(item), depth + 1))
            else:
                invalid = 'object_limit'
                break
            if cost > MAX_RECORD:
                invalid = 'record_limit'
                break
        if not invalid and cost > remaining:
            break  # Retry this intact older record with a fresh page budget.
        position = index
        examined += 1
        if invalid:
            gaps.append({'index': index, 'reason': invalid})
        else:
            accepted.append(value)
            remaining -= cost
        if nodes < 0:
            break
    accepted.reverse()
    gaps.reverse()
    return accepted, gaps, position
