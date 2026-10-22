"""Synthetic Markdown sent unchanged through the real peer transport."""
TEXT = (
    '# Navigation review\n\n'
    'First paragraph with **strong**, *emphasis* and `inline_code`.\n'
    'This is a real soft line break.\n\n'
    'Second paragraph is separate.\n\n'
    '## Ordered checks\n\n1. Keep the draft\n2. Keep the attachment\n\n'
    '- Read only\n- No worker start\n\n'
    '> A peer report is not a human approval.\n> Keep the quotation.\n\n'
    '```python\ndef sample():\n    return "café α"\n\nprint(sample())\n```\n\n'
    '| Case | Result |\n| --- | --- |\n| preview | read only |\n\n'
    '[Safe reference](https://example.invalid/review)\n\n'
    '[Unsafe reference](javascript:alert(1))\n\n'
    '<img src=x onerror="globalThis.peerXss=true">\n\n'
    'Literal backslash-n stays literal: \\n, not a newline.\n'
)
LONG = TEXT + '\n'.join('Retained paragraph %d: exact Unicode α and whitespace.\n' % i for i in range(180)) + '\n## Beyond compact boundary\n\n```text\n    preserved indentation\nsecond line\n```\n'
CRLF = '## CRLF original\r\n\r\nFirst line\r\nSecond line\r\n\r\n  literal \\n remains\r\n'
