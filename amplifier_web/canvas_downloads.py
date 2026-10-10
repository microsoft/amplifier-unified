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
    # Images currently export their encoded source, rather than image bytes.
    # Keep that format's truthful text extension; do not label it PNG/JPEG.
    path = canvas.get('path') if kind not in {'image', 'browser'} else None
    if not isinstance(path, str):
        return fallback
    name = re.split(r'[/\\]', path)[-1]
    name = re.sub(r'[\x00-\x1f\x7f<>:"|?*]', '_', name).rstrip(' .')
    return name if name and len(name.encode('utf-8')) <= 240 else fallback
