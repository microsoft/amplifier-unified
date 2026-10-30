"""Atomic replacement shared by the capacity and execution policy files.

Callers retain validation, private-file reads, locks and revision checks.
"""
import json
import os
import uuid


def write(path, policy, *, size_error):
    # Replacement is atomic; no partial policy is visible to admission.
    temporary = path.with_name('.' + path.stem + '-' + uuid.uuid4().hex)
    content = json.dumps(policy, sort_keys=True).encode()
    if len(content) > 65536:
        raise ValueError(size_error)
    try:
        with os.fdopen(os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        # Windows has no directory-open/fsync contract. The same portable
        # replacement convention is used by the app's settings publishers.
        if hasattr(os, 'O_DIRECTORY'):
            descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)
