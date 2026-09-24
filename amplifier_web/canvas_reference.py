"""Explicit source-backed quotes into one client's unsent composer."""
import html
import re

LIMIT = 4000
ESCAPES = re.compile(r'\\([!"#$%&\'()*+,\-./:;<=>?@\[\]\\^_`{|}~])|&(?:#[xX][0-9a-fA-F]{1,6}|#[0-9]{1,7}|[A-Za-z][A-Za-z0-9]+);')


def literal(text):
    return ESCAPES.sub(lambda m: m.group(1) if m.group(1) is not None else html.unescape(m.group()), text).replace('\r\n', '\n')


def definitions(schema, string):
    span = schema({'start': {'type': 'integer', 'minimum': 0}, 'end': {'type': 'integer', 'minimum': 1},
                   'literal': {'type': 'boolean'}}, ['start', 'end'])
    return {'canvas.reference': ('Append an explicit Markdown/plain-text quote to the chosen client draft, never send it. Read the exact saved version first. Supply its excerpt and ordered source spans (Unicode character offsets, end exclusive); Markdown text escapes are decoded unless literal is true. expectedDraft must match the current draft. Passive selection does nothing.',
        schema({'id': string(100), 'sessionId': string(200), 'clientId': string(100),
                'version': {'type': 'integer', 'minimum': 1}, 'excerpt': string(LIMIT),
                'spans': {'type': 'array', 'minItems': 1, 'maxItems': 128, 'items': span},
                'expectedDraft': {'type': 'string'}}, ['id', 'sessionId', 'version', 'excerpt', 'spans', 'expectedDraft']))}


def command(service, args):
    from .service import AppError
    from .canvas_versions import definition, reference
    from .state_storage import resource
    state = service.state
    sid = args['sessionId']
    if service.clients.record() is None or state.get('selectedSessionId') != sid:
        raise AppError('Choose a browser displaying this conversation before adding a reference.', 409)
    row = next((r for r in state.get('canvasArtifacts', []) if r['id'] == args['id'] and r.get('sessionId') == sid), None)
    if row is None:
        raise AppError('This artifact belongs to another conversation or is unavailable.', 404)
    saved = definition(row, args['version'], service.db)
    if saved['kind'] not in {'markdown', 'text'}:
        raise AppError('Select text from a Markdown or plain-text document.')
    source = resource(service.db, saved['body']['$resource']).get('content', '')
    excerpt = args['excerpt'].strip()
    parts, end = [], 0
    for span in args['spans']:
        start, stop = span['start'], span['end']
        if start < end or stop <= start or stop > len(source) or stop - start > LIMIT * 12:
            raise AppError('The text selection no longer matches the saved source. Select it again.', 409)
        value = source[start:stop]
        parts.append(value if saved['kind'] == 'text' or span.get('literal') else literal(value))
        end = stop
    # Rendering changes block whitespace, but every quoted character must be
    # witnessed by ordered spans in this exact immutable source. Never locate
    # repeated text with find(), or silently bind it to Latest.
    normalized = ' '.join(excerpt.split())
    offset, matches = 0, True
    for part in parts:
        value = ' '.join(part.split())
        if not value:
            continue
        if offset < len(normalized) and normalized[offset] == ' ':
            offset += 1
        if not normalized.startswith(value, offset):
            matches = False
            break
        offset += len(value)
    if not excerpt or not matches or offset != len(normalized):
        raise AppError('The quote does not match the selected saved text. Select it again.', 409)
    draft = state['view'].get('draft', '')
    if draft != args['expectedDraft']:
        raise AppError('The draft changed. Your text was kept; add the reference again.', 409, code='draft_changed')
    title = re.sub(r'([\\`*_{}\[\]()<>#!|])', r'\\\1', ' '.join(saved.get('title', 'Document').split()))
    link = reference(row, args['version'])
    # The selected rendered text remains literal when the draft is later
    # rendered as Markdown (including escaped stars, brackets and image text).
    quoted = re.sub(r'([\\`*_{}\[\]()<>#!|+\-.&])', r'\\\1', excerpt)
    quote = f'From [{title} · Version {args["version"]}]({link}):\n\n' + '\n'.join('> ' + line for line in quoted.splitlines())
    separator = '' if not draft or draft.endswith('\n\n') else '\n' if draft.endswith('\n') else '\n\n'
    updated = draft + separator + quote + '\n\n'
    service.clients.draft(sid, updated)
    return {'status': 'referenced', 'id': row['id'], 'sessionId': sid, 'version': args['version'],
            'reference': link, 'excerpt': excerpt, 'spans': args['spans'], 'draft': updated, 'sent': False}
