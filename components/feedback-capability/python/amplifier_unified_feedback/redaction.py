import os
import re
from pathlib import Path
SECRET_KEY = re.compile(r"token|password|secret|credential|api.?key", re.I)
def redact(text, paths=()):
    """Best effort only: preserve all unredacted text, never truncate silently."""
    counts = {}
    def replace(pattern, label, flags=0):
        nonlocal text
        text, count = re.subn(pattern, '[' + label + ' REDACTED]', text, flags=flags)
        if count:
            counts[label] = counts.get(label, 0) + count
    # Known environment credentials; values never enter a report or log.
    for name, value in os.environ.items():
        if SECRET_KEY.search(name) and len(value) >= 8:
            replace(re.escape(value), 'KNOWN SECRET')
    replace(r'-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----', 'PRIVATE KEY', re.S)
    replace(r'(?i)\bBearer\s+[^\s\"\'<>]+', 'TOKEN')
    replace(r'(?i)\b(?:api[_-]?key|password|secret|access[_-]?token|refresh[_-]?token)\b[\"\']?\s*[:=]\s*[\"\']?[^\s,;\"\'<>]+', 'CREDENTIAL')
    replace(r'\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9_]{16,}|github_pat_[A-Za-z0-9_]{16,})', 'TOKEN')
    replace(r'https?://[^\s<>\"`)]+', 'URL')
    for path in sorted({str(Path.home()), *paths} - {'', '/'}, key=len, reverse=True):
        replace(re.escape(path) + r'[^\s\"\'<>`)]*', 'PATH')
    replace(r'(?<![\w:/])(?:[A-Za-z]:\\|\\\\|/)[^\s\"\'<>`)]+', 'PATH')
    replace(r'(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b', 'EMAIL')
    replace(r'https?://[^\s<>\"`)]+', 'URL')
    warnings = ['Review every line. Detection is incomplete; names, private business information and other sensitive text may remain.']
    if re.search(r'\b(?:\d[ -]?){9,16}\b', text):
        warnings.append('Possible phone, account or other identifying number remains; remove it unless essential.')
    return text, [{'kind': key, 'count': value} for key, value in counts.items()], warnings
