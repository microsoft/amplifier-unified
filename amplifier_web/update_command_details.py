"""Bounded update-command capture and best-effort LOCAL-ONLY explanations.

Text from an installer is not safe telemetry, even after redaction. Never put
these excerpts in diagnostic_facts, app state, feedback or exported reports.
"""
from __future__ import annotations

import re
import os
import unicodedata

STDOUT_LIMIT = 16 * 1024 * 1024
STDERR_LIMIT = 128 * 1024
EXCERPT_LIMIT = 8192
DETAIL_SLOTS = 16
SECRET = re.compile(r'(?i)authorization|cookie|password|passwd|secret|token|api[ _-]?key|credential|extraheader|-----BEGIN .*PRIVATE KEY')


class Capture:
    """Drain a pipe to EOF while retaining only a prefix and an exact count."""
    def __init__(self, limit):
        self.limit = limit
        self.data = bytearray()
        self.total = 0
        self.auth_failure = self.github = self.access_probe = False
        self.missing_git_blob = self.failed_git_reset = False
        self._tail = b''

    async def drain(self, reader):
        while chunk := await reader.read(65536):
            self.total += len(chunk)
            self.data.extend(chunk[:max(0, self.limit - len(self.data))])
            # Preserve the existing retry predicate after retention fills.
            # Only booleans survive this scan; no new published cause classifier.
            from .reset import repository_auth_failure
            from .update_diagnostics import probe_record
            window = self._tail + chunk
            self.missing_git_blob |= b'error: unable to read sha1 file of ' in window
            self.failed_git_reset |= b'fatal: Could not reset index file to revision' in window
            self.auth_failure |= repository_auth_failure(window)
            self.github |= b'github.com' in window.lower()
            self.access_probe |= any((probe_record(line) or {}).get('repositoryAccessFailure') is True
                                     for line in window.splitlines())
            self._tail = window[-8192:]

    @property
    def truncated(self):
        return self.total > len(self.data)


def local_excerpt(capture, environment):
    """Redact BEFORE display truncation; this is not a guarantee of anonymity."""
    # surrogateescape preserves raw C1 bytes so their payloads are removed too,
    # without mistaking UTF-8 continuation bytes for terminal introducers.
    text = bytes(capture.data).decode('utf-8', errors='surrogateescape')
    if capture.truncated:
        # A cut URL/header/value must never become an apparently harmless suffix.
        text = text.rpartition('\n')[0] if '\n' in text else ''
    # Remove whole terminal strings (OSC/DCS/APC/PM/SOS), including unfinished
    # ones, before CSI and single-character escapes. Do not reveal OSC payloads.
    text = re.sub(r'(?:\x1b\]|\x9d|\udc9d).*?(?:\x07|\x1b\\|\x9c|\udc9c|$)', '', text, flags=re.S)
    text = re.sub(r'(?:\x1b[P_^X]|[\x90\x98\x9e\x9f\udc90\udc98\udc9e\udc9f]).*?(?:\x1b\\|\x9c|\udc9c|$)', '', text, flags=re.S)
    text = re.sub(r'(?:\x1b\[|\x9b|\udc9b)[0-?]*[ -/]*[@-~]', '', text)
    text = re.sub(r'\x1b(?:[ -/]*[@-~]|.*$)', '', text, flags=re.S)
    # Join split labels/values before redacting; retain only newline and tab.
    text = ''.join(c for c in text if c in '\n\t' or unicodedata.category(c)[0] != 'C')
    for name, value in environment.items():
        if SECRET.search(name) and value:
            text = text.replace(value, '[REDACTED]')
    lines, sensitive_block = [], False
    for line in text.split('\n'):
        if SECRET.search(line):
            sensitive_block = True
            lines.append('[credential-bearing line omitted]')
        elif sensitive_block:
            # Installers can wrap credentials without indentation or a closing
            # delimiter. Prefer losing the remainder to saving a continuation.
            lines.append('[credential continuation omitted]')
        else:
            lines.append(line)
    text = '\n'.join(lines)
    # Entire URLs, not just userinfo: paths, queries and fragments can be secrets.
    text = re.sub(r'(?i)(?:[a-z][a-z0-9+.-]*://|git@)[^\s<>"\']+', '[URL omitted]', text)
    # Omit the remainder of a path-bearing line: quoted/spaced paths and wrapped
    # fragments should not expose account/folder names as apparently safe prose.
    text = re.sub(r'(?im)(?:[a-z]:[\\/]|\\\\|~[/\\]|(?<!\w)/)[^\n]*', '[path omitted]', text)
    encoded = text.encode('utf-8')
    excerpt = encoded[:EXCERPT_LIMIT].decode('utf-8', errors='ignore')
    return {'stderr': excerpt, 'stderrTruncated': capture.truncated,
            'excerptTruncated': len(encoded) > EXCERPT_LIMIT}


def retain(diagnostics, value, command_id, phase):
    """Write one bounded private slot, never a shared event or automatic upload."""
    try:
        return _retain(diagnostics, value, command_id, phase)
    except Exception:
        # Optional explanations must never replace the operation's outcome,
        # including malformed attributes from a wrapped third-party exception.
        return {'localDetailStatus': 'unavailable'}


def _retain(diagnostics, value, command_id, phase):
    details = getattr(value, 'local_command_details', None)
    if not isinstance(details, list) or not details:
        return {}
    from .host.config import write_private
    import json
    sequence = diagnostics.state.get('localDetailSequence', 0)
    if type(sequence) is not int or not 0 <= sequence < 10**15:
        sequence = 0
    slot = sequence % DETAIL_SLOTS
    diagnostics.state['localDetailSequence'] = sequence + 1
    from .update_diagnostics import command_attempts
    safe_details = []
    for row in details[:2]:
        if not isinstance(row, dict):
            continue
        capture = Capture(STDERR_LIMIT)
        text = row.get('stderr', '')
        if not isinstance(text, str):
            text = ''
        prefix = text[:STDERR_LIMIT].encode(errors='replace')
        capture.data.extend(prefix[:STDERR_LIMIT])
        capture.total = len(prefix) + int(len(text) > STDERR_LIMIT)
        safe = (command_attempts([row]) or [{}])[0]
        safe_details.append({**safe, **local_excerpt(capture, os.environ),
                             'stderrTruncated': row.get('stderrTruncated') is True,
                             'excerptTruncated': row.get('excerptTruncated') is True or len(text) > EXCERPT_LIMIT})
    payload = {'commandId': command_id, 'attemptId': diagnostics.state.get('attemptId'),
               'phase': phase, 'privacy': 'LOCAL ONLY. Redaction is best effort. Review before sharing.',
               'commandAttempts': safe_details}
    try:
        folder = diagnostics.manager.directory / 'command-details'
        if folder.is_symlink():
            raise ValueError('Local details directory is a link')
        write_private(folder / f'{slot:02}.json',
                      json.dumps(payload, ensure_ascii=True, indent=2),
                      owned_root=diagnostics.manager.home)
    except (OSError, ValueError, RuntimeError):
        return {'localDetailStatus': 'unavailable'}
    return {'localDetailStatus': 'saved', 'localDetailSlot': slot}


def guidance(event):
    if (event.get('localDetailStatus') == 'saved'
            and type(event.get('localDetailSlot')) is int and 0 <= event['localDetailSlot'] < DETAIL_SLOTS):
        return (f" Redacted command details are saved only on this computer in "
                f"updates/command-details/{event['localDetailSlot']:02}.json under the app data folder. "
                "Review before sharing; this text is excluded from shared diagnostics. "
                "Later commands can reuse this slot; match its command and attempt IDs. "
                "To locate the data folder, run amplifier-unified doctor --verbose on this computer.")
    if event.get('localDetailStatus') == 'unavailable':
        return " Local command details could not be saved. Do not retry an installation just to recover its explanation."
    return ''
