"""Small read-only labels; T2 reuses process-owned checked source indexes."""
import json
import os
import re
import stat

from .naming import directory_for, request_text
from .shared_state_probe import text_content

MAX_BYTES = 256 * 1024
MAX_LINES = 128
MAX_TITLE = 96


def unnamed(session):
    if session.get('titleSource') in {'manual', 'generated'} or session.get('nativeNameSource') in {'manual', 'generated'}:
        return False
    return bool(re.fullmatch(r'Conversation [a-zA-Z0-9-]{8}', session.get('title', '')))


def read(home, session):
    """Return only an excerpt, without opening a worker or changing metadata."""
    if not unnamed(session):
        return {'title': session.get('title', ''), 'source': 'saved'}
    return first_request(home, session)


def first_request(home, session, max_chars=MAX_TITLE):
    """Read a bounded user-request excerpt for a preview or background name."""
    directory = directory_for(home, session)
    from .committed_source import versioned, background_sources, opening
    if versioned(directory):
        pool = background_sources()
        owner = {'project': str(directory.parent), 'nativeId': directory.name}
        try:
            with pool.source(directory, owner) as source:
                for _, row in opening(source, max_bytes=MAX_BYTES, max_rows=MAX_LINES):
                    title = _request(row, max_chars)
                    if title:
                        return {'title': title, 'source': 'first-message'}
        except (OSError, ValueError):
            pass
        return {'title': '', 'source': 'unnamed'}
    for filename in ('transcript.jsonl', 'transcript.jsonl.backup'):
        try:
            fd = os.open(directory / filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            continue
        except OSError:
            break
        with os.fdopen(fd, 'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                break
            remaining = MAX_BYTES
            for _ in range(MAX_LINES):
                line = stream.readline(remaining + 1)
                if not line or len(line) > remaining:
                    break
                remaining -= len(line)
                try:
                    row = json.loads(line)
                except (ValueError, UnicodeError):
                    continue
                title = _request(row, max_chars)
                if title:
                    return {'title': title, 'source': 'first-message'}
        # An existing primary transcript, even empty, wins over its backup.
        break
    return {'title': '', 'source': 'unnamed'}


def _request(row, max_chars):
    if not isinstance(row, dict) or row.get('role') != 'user':
        return None
    metadata = row.get('metadata') or {}
    if not isinstance(metadata, dict):
        return None
    provenance = metadata.get('amplifier_input') or {}
    if metadata.get('ephemeral') or metadata.get('live_recovery_job') or row.get('observation'):
        return None
    if isinstance(provenance, dict) and provenance.get('kind') not in {None, 'user'}:
        return None
    text = ' '.join(request_text(text_content(row)).split())
    if text:
        return text[:max_chars].rstrip() + ('…' if len(text) > max_chars else '')
