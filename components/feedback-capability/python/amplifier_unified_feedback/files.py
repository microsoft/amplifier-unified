"""Feedback owns only explicitly staged immutable bytes, never client drafts."""
import hashlib
import os
from pathlib import Path
import re
import uuid

MAX_FILE = 8 * 1024 * 1024

def location(home, identity):
    if not re.fullmatch(r'[a-f0-9]{32}', identity):
        raise ValueError('Invalid attachment identity')
    root = Path(home) / 'attachments'
    if root.is_symlink() or (root / identity).is_symlink():
        raise ValueError('Attachment storage changed')
    return root / identity

def save(home, name, data, mime='text/plain'):
    if not data or len(data) > MAX_FILE:
        raise ValueError('Choose a nonempty attachment up to 8 MiB')
    name = ''.join(c for c in Path(name.replace('\\', '/')).name[:200] if ord(c) >= 32 and ord(c) != 127)
    if name in {'', '.', '..'}:
        name = 'attachment'
    identity = uuid.uuid4().hex
    path = location(home, identity)
    path.mkdir(parents=True, mode=0o700)
    with (path / 'content').open('xb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    (path / 'content').chmod(0o400)
    return {'id': identity, 'name': name, 'size': len(data), 'mime': mime, 'sha256': hashlib.sha256(data).hexdigest()}
