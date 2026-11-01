"""Typed image transport fingerprints for diagnostic copies, never live requests."""
import hashlib


# Context comes from known provider wire containers. A matching dictionary in
# tool arguments, schemas, metadata or ordinary text is not an image block.
def child_context(parent, key, context):
    if context == 'root':
        return {'messages': 'messages', 'input': 'messages', 'output': 'messages',
                'content': 'blocks', 'contents': 'gemini-messages'}.get(key)
    if context == 'message':
        if key == 'content':
            return 'blocks'
        if key == 'images' and parent.get('role') in {'user', 'assistant', 'tool'}:
            return 'ollama-images'
        if key == 'result' and parent.get('type') == 'image_generation_call':
            return ('image', 'base64', None)
    if context == 'block':
        kind = parent.get('type')
        if key == 'image_url' and kind == 'input_image':
            return ('image', 'data-url', None)
        if key == 'image_url' and kind == 'image_url':
            return 'image-url'
        if key == 'source' and kind == 'image':
            return 'image-source'
        if key == 'content' and kind == 'tool_result':
            return 'blocks'
    if context == 'image-url' and key == 'url':
        return ('image', 'data-url', None)
    if context == 'image-source' and key == 'data' and parent.get('type') == 'base64':
        media_type = parent.get('media_type')
        if isinstance(media_type, str) and media_type.startswith('image/'):
            return ('image', 'base64', media_type)
    if context == 'gemini-message' and key == 'parts':
        return 'gemini-parts'
    if context == 'gemini-part' and key in {'inline_data', 'inlineData'}:
        return 'inline-image'
    if context == 'inline-image' and key == 'data':
        media_type = parent.get('mime_type', parent.get('mimeType'))
        if isinstance(media_type, str) and media_type.startswith('image/'):
            return ('image', 'base64', media_type)
    return None


def element_context(context):
    return {'messages': 'message', 'blocks': 'block',
            'gemini-messages': 'gemini-message', 'gemini-parts': 'gemini-part',
            'ollama-images': ('image', 'base64', None)}.get(context)


def fingerprint(value, context):
    if not isinstance(context, tuple) or context[0] != 'image':
        return None
    if not isinstance(value, (str, bytes)):
        return None
    _, encoding, media_type = context
    if encoding == 'data-url':
        if not isinstance(value, str):
            return None
        # Only inline image data, not remote URLs or an ordinary string.
        header = value[:256].partition(',')[0]
        if not header.startswith('data:image/') or not header.endswith(';base64'):
            return None
        media_type = header[5:-7]
    digest = hashlib.sha256()
    # Hash without allocating another multi-megabyte encoded image string.
    for offset in range(0, len(value), 64 * 1024):
        part = value[offset:offset + 64 * 1024]
        digest.update(part.encode('utf-8') if isinstance(part, str) else part)
    result = {'omitted': True, 'encoding': encoding if isinstance(value, str) else 'bytes',
              'characters' if isinstance(value, str) else 'bytes': len(value),
              'sha256': digest.hexdigest()}
    if media_type is not None:
        result['media_type'] = media_type[:200]
    # A fingerprint describes the omitted transport. It is not a resolvable
    # attachment reference, and does not imply a canonical image was saved.
    return {'__unified_image_transport__': result}
