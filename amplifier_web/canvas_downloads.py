"""Names for saved Canvas sources, shared by UI, agents and HTTP downloads."""
import re
import base64


def image_bytes(canvas):
    """Decode only the immutable, validated image snapshot, never its path."""
    from .workspace_canvas import _image
    content = canvas.get('content', '')
    data = base64.b64decode(content.split(';base64,', 1)[1], validate=True)
    normalized = _image(data)
    return data, normalized.split(';', 1)[0][5:]


def filename(canvas):
    kind = canvas.get('kind')
    if kind == 'image' and canvas.get('content'):
        _, mime = image_bytes(canvas)
        return 'canvas.' + {'image/png': 'png', 'image/jpeg': 'jpg', 'image/webp': 'webp', 'image/gif': 'gif'}[mime]
    fallback = 'canvas-3d.html' if kind == 'babylon' else 'canvas.' + {
        'markdown': 'md', 'html': 'html', 'mermaid': 'mmd', 'dot': 'dot',
        'json': 'json', 'jsonl': 'jsonl', 'a2ui': 'json',
    }.get(kind, 'txt')
    path = canvas.get('path') if kind not in {'image', 'browser'} else None
    def safe_name(value):
        return re.sub(r'[\x00-\x1f\x7f<>:"|?*/\\]', '_', value).rstrip(' .')
    if isinstance(path, str):
        name = safe_name(re.split(r'[/\\]', path)[-1])
        return name if name and len(name.encode('utf-8')) <= 240 else fallback
    title = canvas.get('title')
    if not isinstance(title, str) or not title.strip() or title == 'Canvas':
        return fallback
    extension = fallback.rsplit('.', 1)[-1]
    name = safe_name(title).lstrip(' .')
    if not name:
        return fallback
    if not name.lower().endswith('.' + extension):
        name += '.' + extension
    # Preserve a truthful extension, including for long Unicode titles.
    if len(name.encode('utf-8')) > 240:
        stem = name[:-(len(extension) + 1)].encode('utf-8')[:230].decode('utf-8', errors='ignore').rstrip(' .')
        name = stem + '.' + extension
    return name
