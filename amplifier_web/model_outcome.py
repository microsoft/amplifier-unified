"""Small display facts from the normalized response. Never copy model or tool content."""


def public_outcome(value):
    if not isinstance(value, dict):
        return {}
    facts = {key: value[key] for key in ('fileReads', 'commands', 'otherTools')
             if type(value.get(key)) is int and 0 < value[key] <= 2**31 - 1}
    if value.get('text') is True:
        facts['text'] = True
    return {'modelOutcome': facts} if facts else {}


def _field(record, key):
    return record.get(key) if isinstance(record, dict) else getattr(record, key, None)


def response_outcome(response):
    # ChatResponse can carry the same requests in both tool_calls and content.
    # The structured call list takes precedence. Tool arguments are never read.
    calls = _field(response, 'tool_calls')
    content = _field(response, 'content')
    blocks = content if isinstance(content, (list, tuple)) else ()
    facts = {}
    if isinstance(calls, (list, tuple)) and calls:
        if len(calls) > 256:
            return public_outcome({'otherTools': len(calls)})
    else:
        if len(blocks) > 256:
            return {}
        calls = (block for block in blocks if _field(block, 'type') == 'tool_call')
    for call in calls:
        name = _field(call, 'name')
        key = 'fileReads' if name == 'read_file' else 'commands' if name == 'bash' else 'otherTools'
        facts[key] = facts.get(key, 0) + 1
    if not facts and any(_field(block, 'type') == 'text'
                         and _field(block, 'visibility') in (None, 'user')
                         and isinstance(_field(block, 'text'), str) and _field(block, 'text')
                         for block in blocks[:256]):
        facts['text'] = True
    return public_outcome(facts)
