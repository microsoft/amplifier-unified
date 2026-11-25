"""Detach user-uploaded images for consumers that cannot open this session's store."""
import base64
from contextlib import closing


def inline_content(content, refs, image_source):
    """Replace issued image markers in order, using the caller's source authority."""
    blocks = list(content)
    start = 0
    for ref in refs:
        marker = {'type': 'text', 'text': f"[Session image {ref['sha256']}]"}
        try:
            pos = blocks.index(marker, start)
        except ValueError:
            raise ValueError('The saved image positions do not match the original input') from None
        blocks[pos] = {'type': 'image', 'source': image_source(ref)}
        start = pos + 1
    return blocks


def _uploads(row):
    from .host.media import NAMESPACE
    carrier = (row.get('metadata') or {}).get(NAMESPACE)
    if not isinstance(carrier, dict):
        return []
    refs = carrier.get('refs', [])
    if isinstance(refs, list):
        for ref in refs:
            origin = ref.get('provenance') if isinstance(ref, dict) else None
            if isinstance(origin, dict) and origin.get('kind') == 'upload':
                return refs
    return []


def inline_composer_uploads(directory, rows, *, saved=None, limit=None):
    """Materialize issued occurrences in detached rows, leaving other media untouched.

    Rows are read from the selected native transcript. A live fork additionally
    supplies the saved rows, so an uncheckpointed input cannot cross that boundary.
    """
    from .host.media import NAMESPACE, RequestOnlyRegistry
    selected = [(index, row, refs) for index, row in enumerate(rows) if (refs := _uploads(row))]
    if not selected:
        return rows
    from amplifier_foundation.session.media import SessionMediaStore

    issued = RequestOnlyRegistry(directory / 'media-upload-issuance.json')
    originals = {}
    for row in rows if saved is None else saved:
        input_id = ((row.get('metadata') or {}).get('amplifier_input') or {}).get('id')
        if isinstance(input_id, str):
            originals[input_id] = None if input_id in originals else row
    result = list(rows)
    image_bytes = 0

    def authorize(candidate, *, purpose):
        return (purpose == 'transfer' and candidate in refs
                and store.get_origin_classification(candidate) == 'composer-upload')

    with closing(SessionMediaStore(directory / 'media', directory.name, read_only=True,
                                   source_read_verifier=authorize)) as store:
        for index, row, refs in selected:
            metadata, content = row['metadata'], row.get('content')
            source = metadata.get('amplifier_input') or {}
            input_id = source.get('id')
            if (row.get('role') != 'user' or source.get('kind') not in {'user', 'steer'}
                    or source.get('source', 'user') != 'user'
                    or not isinstance(content, list) or set(metadata[NAMESPACE]) != {'refs'}
                    or not issued.issued_composer_refs(input_id, content, refs)
                    or originals.pop(input_id, None) != row):
                raise ValueError('The saved conversation cannot confirm the original image input')

            def image_source(ref):
                nonlocal image_bytes
                origin = ref['provenance']
                if (origin['kind'] != 'upload' or origin['session_id'] != directory.name
                        or ref['session_id'] != directory.name or origin['expires_at'] is not None
                        or ref['mime'] not in {'image/png', 'image/jpeg'}):
                    raise ValueError("Only this conversation's durable composer images can be handed off")
                image_bytes += 4 * ((ref['bytes'] + 2) // 3)
                if limit is not None and image_bytes > limit:
                    raise ValueError('Detached images exceed the task transfer limit')
                with store.resolve_published_ref(ref) as snapshot:
                    data = base64.b64encode(snapshot.read()).decode('ascii')
                return {'type': 'base64', 'media_type': ref['mime'], 'data': data}

            blocks = inline_content(content, refs, image_source)
            result[index] = {**row, 'content': blocks,
                             'metadata': {k: v for k, v in metadata.items() if k != NAMESPACE}}
    return result
