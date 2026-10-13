"""Private, immutable large diagnostic copies beside their native event log.

The event owns the reference. Neither browser arguments nor provider data may
select a file path. These files contain redacted provider events, not rebuilt
requests, and are diagnostic artifacts rather than conversation history.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile

MAX_BYTES = 64 * 1024 * 1024
MAX_CHARS = 16 * 1024 * 1024
DIRECTORY = 'provider-payloads'


def save(directory, value):
    from .provider_recording import redact_request
    safe, truncated = redact_request(value, limit=MAX_CHARS)
    directory = Path(directory) / DIRECTORY
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if directory.is_symlink():
        raise ValueError('Capture directory must not be a link')
    temporary = None
    try:
        digest, size = hashlib.sha256(), 0
        with tempfile.NamedTemporaryFile(dir=directory, delete=False) as stream:
            temporary = Path(stream.name)
            for part in json.JSONEncoder(ensure_ascii=False).iterencode(safe):
                raw = part.encode('utf-8')
                size += len(raw)
                if size > MAX_BYTES:
                    raise ValueError('Encoded provider record exceeds its storage limit')
                digest.update(raw)
                stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        identity = digest.hexdigest()
        os.replace(temporary, directory / (identity + '.json'))
        return {'sha256': identity, 'bytes': size, 'redacted': True,
                'truncated': truncated, 'limit_chars': MAX_CHARS}
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def metadata(value):
    if not isinstance(value, dict) or not re.fullmatch('[0-9a-f]{64}', str(value.get('sha256', ''))):
        return None
    if type(value.get('bytes')) is not int or not 0 < value['bytes'] <= MAX_BYTES:
        return None
    if value.get('redacted') is not True or type(value.get('truncated')) is not bool:
        return None
    return {key: value[key] for key in ('sha256', 'bytes', 'redacted', 'truncated', 'limit_chars') if key in value}


def open_record(event_reference, expected_digest):
    from .event_log_view import read_event_data
    data = read_event_data(event_reference)
    files = data.get('provider_payloads')
    info = metadata(files.get(event_reference['key'])) if isinstance(files, dict) else None
    if not info or info['sha256'] != expected_digest:
        raise ValueError('The provider recording changed. Refresh this conversation.')
    directory = Path(event_reference['path']).parent / DIRECTORY
    if directory.is_symlink():
        raise ValueError('The provider recording is unavailable.')
    try:
        path = directory / (info['sha256'] + '.json')
        if path.is_symlink():
            raise ValueError('The provider recording is unavailable.')
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
        stream = os.fdopen(fd, 'rb')
    except OSError as exc:
        raise ValueError('The provider recording is unavailable on this host.') from exc
    try:
        stamp = os.fstat(stream.fileno())
        if not stat.S_ISREG(stamp.st_mode) or stamp.st_size != info['bytes']:
            raise ValueError('The provider recording changed.')
        digest = hashlib.sha256()
        while chunk := stream.read(64 * 1024):
            digest.update(chunk)
        if digest.hexdigest() != info['sha256']:
            raise ValueError('The provider recording changed.')
        stream.seek(0)
        return stream, info
    except BaseException:
        stream.close()
        raise
