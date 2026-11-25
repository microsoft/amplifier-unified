"""Listener-owned PWA presentation. Never a workspace or install identity."""
from __future__ import annotations

import base64
import binascii
from collections import OrderedDict
import hashlib
import html
import io
import json
import os
from pathlib import Path
import re
import socket
import stat
import struct
import threading
import unicodedata
import warnings

from .deployment import canonical_host, write_private

MAX_IMAGE_BYTES = 2 * 1024 * 1024
MAX_IMAGE_TEXT = 4 * ((MAX_IMAGE_BYTES + 2) // 3)
SIZES = (32, 180, 192, 512)
# Light Reference accent for requests made before a browser supplies its own.
REFERENCE_ACCENT = '#48646d'
_ICON_GENERATION = 'rounded-original-v2'
_ICON_CACHE_LIMIT = 32
# Match the existing native private-file read AND publication boundary.
_RECORD_LIMIT = 1024 * 1024
_WINDOWS = os.name == 'nt'


def _record_bytes(record):
    content = json.dumps(record, ensure_ascii=True).encode('ascii')
    if len(content) > _RECORD_LIMIT:
        raise ValueError('App name and icon are too large to save. Choose a smaller icon.')
    return content


def _image_text_budget():
    # Every valid name has at most 120 code points. A non-BMP character needs
    # twelve ASCII bytes when escaped; reserve that plus the complete JSON and
    # color overhead even when this upload arrives before a later name edit.
    largest_labels = {'version': 1, 'name': '\U0001f600' * 120,
                      'color': '#ffffff', 'image': ''}
    return _RECORD_LIMIT - len(json.dumps(largest_labels, ensure_ascii=True).encode('ascii'))


def _record_absent(path):
    """Observe absence without creating paths or imposing a private-root ACL.

    This preflight never reads stored content. Any existing record, link,
    junction or inaccessible component still goes through the native boundary.
    """
    for part in reversed([path, *path.parents]):
        try:
            info = part.lstat()
        except FileNotFoundError:
            return True
        if (stat.S_ISLNK(info.st_mode)
                or getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
            return False
    return False


def definitions(schema, string):
    return {
        'instanceBranding.get': (
            'Read this server listener’s app name and icon. No files are created.',
            schema()),
        'instanceBranding.update': (
            'Change this server listener’s app name or icon for every browser. '
            'An empty name uses the default; an empty color uses the browser appearance accent. '
            'Image is raw base64 PNG, JPEG, WebP, GIF or ICO '
            '(up to 2 MB and 4096 × 4096). An empty image returns to the colored badge. '
            'Requires global host-defaults authority.',
            schema({'patch': schema({
                'name': string(120),
                'color': {'type': 'string', 'pattern': '^(?:#[0-9a-fA-F]{6})?$'},
                'image': string(MAX_IMAGE_TEXT),
            }, [])}, ['patch'])),
        'instanceBranding.reset': (
            'Reset only this server listener’s app name, badge color and uploaded icon to defaults. '
            'Does not change the installed app identity. Requires global host-defaults authority.',
            schema()),
    }


def _name(value):
    if (not isinstance(value, str) or len(value) > 120
            or any(unicodedata.category(char).startswith('C')
                   or unicodedata.category(char) in {'Zl', 'Zp'} for char in value)):
        raise ValueError('Use a single-line app name of up to 120 characters.')
    return value.strip()


def _color(value):
    if not isinstance(value, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', value):
        raise ValueError('Use a six-digit hex color, such as #456abc.')
    return value.lower()


def _accent(value):
    """An untrusted URL parameter, never a persisted listener preference."""
    if isinstance(value, str) and re.fullmatch(r'[0-9a-fA-F]{6}', value):
        return value.lower()
    return None


def _load_mark():
    from PIL import Image
    path = Path(__file__).parent / 'static/branding/pwa/pwa-512.png'
    try:
        with Image.open(path) as source:
            mark = source.convert('RGBA')
            mark.thumbnail((352, 352), Image.Resampling.LANCZOS)
            return mark
    except OSError:
        # Match the existing missing-static behavior: the host can still start,
        # but icon generation fails rather than inventing replacement artwork.
        return None


def _badge(size, color, mark):
    from PIL import Image, ImageDraw
    if mark is None:
        raise FileNotFoundError('Bundled Amplifier icon artwork is unavailable.')
    # The original mark sits directly on the rounded accent, without an
    # outline or backing. Resize the complete tile for antialiased small icons.
    canvas = Image.new('RGBA', (512, 512), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle((0, 0, 511, 511), radius=112, fill=color)
    offset = ((512 - mark.width) // 2, (512 - mark.height) // 2)
    canvas.alpha_composite(mark, offset)
    return canvas.resize((size, size), Image.Resampling.LANCZOS)


def _png(image):
    output = io.BytesIO()
    image.save(output, format='PNG')
    return output.getvalue()


def safe_image(value):
    """Decode a bounded raster, strip metadata/animation and keep at most 512px."""
    from PIL import Image, ImageOps, UnidentifiedImageError
    if not isinstance(value, str) or len(value) > MAX_IMAGE_TEXT:
        raise ValueError('Choose an image of 2 MB or less.')
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        raise ValueError('Choose a valid raster image.') from None
    if not raw or len(raw) > MAX_IMAGE_BYTES:
        raise ValueError('Choose an image of 2 MB or less.')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw), formats=('PNG', 'JPEG', 'WEBP', 'GIF', 'ICO')) as source:
                width, height = source.size
                if (not 1 <= width <= 4096 or not 1 <= height <= 4096
                        or width * height > 16_777_216):
                    raise ValueError('Choose an image no larger than 4096 × 4096 pixels.')
                source.load()
                image = ImageOps.exif_transpose(source).convert('RGBA')
                image.thumbnail((512, 512), Image.Resampling.LANCZOS)
                # A new pixel-only image drops EXIF, text, ICC and other metadata.
                clean = Image.new('RGBA', image.size)
                clean.paste(image)
                budget = _image_text_budget()
                while True:
                    encoded = base64.b64encode(_png(clean)).decode('ascii')
                    if len(encoded) <= budget:
                        return encoded
                    if max(clean.size) == 1:
                        raise ValueError('This icon is too large to save. Choose a smaller image.')
                    clean = clean.resize((max(1, clean.width * 7 // 8),
                                          max(1, clean.height * 7 // 8)),
                                         Image.Resampling.LANCZOS)
    except (UnidentifiedImageError, OSError, SyntaxError, EOFError, OverflowError,
            struct.error, Image.DecompressionBombError,
            Image.DecompressionBombWarning):
        raise ValueError('Choose a valid PNG, JPEG, WebP, GIF or ICO image.') from None


class InstanceBranding:
    def __init__(self, data_dir, config):
        self.root = Path(data_dir)
        listener = [sorted({canonical_host(bind) for bind in config['bind']}), config['port']]
        key = hashlib.sha256(json.dumps(listener, separators=(',', ':')).encode()).hexdigest()
        self.path = self.root / 'config' / 'services' / f'instance-branding-{key}.json'
        hostname = _name(socket.gethostname().split('.')[0][:80]) or 'this computer'
        port = '' if config['port'] in {80, 443} else f":{config['port']}"
        self.default_name = f'Amplifier · {hostname}{port}'
        self.default_color = REFERENCE_ACCENT
        self._icons = OrderedDict()
        self._icon_lock = threading.Lock()
        self._record = self._load()
        # Pin the packaged mark at startup. Snapshot, manifest and icon reads,
        # including uncached browser accents, do not read configuration/assets.
        self._mark = _load_mark()
        self._refresh()

    def _load(self):
        if _WINDOWS and _record_absent(self.path):
            return {'version': 1, 'name': '', 'color': '', 'image': ''}
        try:
            if _WINDOWS:
                from . import windows_files
                with windows_files.directory(self.path.parent, owned_root=self.root) as pinned:
                    raw = pinned.read_private(self.path.name, limit=_RECORD_LIMIT)
            else:
                with self.path.open('rb') as stream:
                    raw = stream.read(_RECORD_LIMIT + 1)
        except FileNotFoundError:
            return {'version': 1, 'name': '', 'color': '', 'image': ''}
        if len(raw) > _RECORD_LIMIT:
            raise ValueError('Saved app branding exceeds its supported size.')
        record = json.loads(raw)
        if (not isinstance(record, dict) or set(record) != {'version', 'name', 'color', 'image'}
                or type(record['version']) is not int or record['version'] != 1
                or any(not isinstance(record[key], str) for key in ('name', 'color', 'image'))):
            raise ValueError('Saved app branding has an unsupported format.')
        record['name'] = _name(record['name'])
        record['color'] = _color(record['color']) if record['color'] else ''
        record['image'] = safe_image(record['image']) if record['image'] else ''
        return record

    def _refresh(self):
        name = self._record['name'] or self.default_name
        color = self._record['color'] or self.default_color
        mode = 'custom' if self._record['color'] else 'appearance'
        revision = hashlib.sha256(json.dumps([_ICON_GENERATION, name, color, mode, self._record['image']],
                                            ensure_ascii=True).encode()).hexdigest()[:24]
        snapshot = {
            'name': name, 'defaultName': self.default_name,
            'color': color, 'defaultColor': self.default_color, 'colorMode': mode,
            'iconUrl': f'/instance-icon/192.png?v={revision}',
            'customIcon': bool(self._record['image']), 'revision': revision,
        }
        with self._icon_lock:
            self._snapshot = snapshot
            self._current = (self._record, snapshot)
            self._icons.clear()

    def snapshot(self):
        return dict(self._snapshot)

    @staticmethod
    def prepare(patch):
        if not isinstance(patch, dict) or set(patch) - {'name', 'color', 'image'}:
            raise ValueError('Choose only app name, badge color or image.')
        result = {}
        if 'name' in patch:
            result['name'] = _name(patch['name'])
        if 'color' in patch:
            result['color'] = '' if patch['color'] == '' else _color(patch['color'])
        if 'image' in patch:
            result['image'] = safe_image(patch['image']) if patch['image'] != '' else ''
        return result

    def update(self, prepared=None, *, reset=False):
        record = ({'version': 1, 'name': '', 'color': '', 'image': ''} if reset
                  else {**self._record, **(prepared or {})})
        # Validate the exact merged bytes before either the private writer or
        # the in-memory snapshot changes. Keep one encoding through publication.
        content = _record_bytes(record)
        if record != self._record:
            write_private(self.path, content, owned_root=self.root)
            self._record = record
            self._refresh()
        return self.snapshot()

    def icon_url(self, size, accent=None, *, snapshot=None):
        snapshot = snapshot or self._snapshot
        accent = _accent(accent)
        suffix = f'&accent={accent}' if accent and snapshot['colorMode'] == 'appearance' and not snapshot['customIcon'] else ''
        return f'/instance-icon/{size}.png?v={snapshot["revision"]}{suffix}'

    def _icon_lookup(self, size, accent):
        if size not in SIZES:
            raise ValueError('Choose an app icon size of 32, 180, 192 or 512.')
        with self._icon_lock:
            current = self._current
            record, snapshot = current
            accent = _accent(accent) if snapshot['colorMode'] == 'appearance' and not record['image'] else None
            color = '#' + accent if accent else snapshot['color']
            key = (snapshot['revision'], size, color if not record['image'] else None)
            cached = self._icons.get(key)
            if cached is not None:
                self._icons.move_to_end(key)
            return current, key, cached

    def cached_icon(self, size, accent=None):
        """Return a current cached PNG without entering the render queue."""
        return self._icon_lookup(size, accent)[2]

    def icon(self, size, accent=None):
        from PIL import Image, ImageOps
        current, key, cached = self._icon_lookup(size, accent)
        if cached is None:
            record, _ = current
            if record['image']:
                with Image.open(io.BytesIO(base64.b64decode(record['image']))) as source:
                    image = ImageOps.pad(source.convert('RGBA'), (size, size),
                                         method=Image.Resampling.LANCZOS, color=(0, 0, 0, 0))
            else:
                image = _badge(size, key[2], self._mark)
            cached = _png(image)
            with self._icon_lock:
                # Object identity also rejects A -> B -> A while encoding.
                if self._current is current:
                    self._icons[key] = cached
                    self._icons.move_to_end(key)
                    while len(self._icons) > _ICON_CACHE_LIMIT:
                        self._icons.popitem(last=False)
        return cached

    def manifest(self, template, accent=None):
        snapshot = self._snapshot
        result = {**template, 'name': snapshot['name'], 'short_name': snapshot['name']}
        result['icons'] = [
            {**icon, 'src': self.icon_url(int(icon['sizes'].split('x')[0]), accent, snapshot=snapshot)}
            if (icon.get('sizes') in {'192x192', '512x512'}
                and 'maskable' not in icon.get('purpose', 'any').split())
            else dict(icon)
            for icon in template['icons']
        ]
        return result

    def document(self, source, *, login=False):
        """Replace only head metadata; custom names never become markup/code."""
        head, closing, body = source.partition('</head>')
        title = html.escape(('Sign in · ' if login else '') + self._snapshot['name'], quote=True)
        head = re.sub(r'<title\b[^>]*>.*?</title>', '', head, flags=re.I | re.S)

        def link(match):
            rel = re.search(r'\brel\s*=\s*["\']([^"\']+)["\']', match[0], re.I)
            return '' if rel and rel[1].lower() in {'icon', 'shortcut icon', 'apple-touch-icon', 'manifest'} else match[0]

        head = re.sub(r'<link\b[^>]*>', link, head, flags=re.I)
        head = re.sub(r'<meta\b(?=[^>]*\bname=["\'](?:apple-mobile-web-app-title|application-name)["\'])[^>]*>',
                      '', head, flags=re.I)
        metadata = (f'<title>{title}</title>\n'
                    f'<meta name="application-name" content="{html.escape(self._snapshot["name"], quote=True)}">\n'
                    f'<meta name="apple-mobile-web-app-title" content="{html.escape(self._snapshot["name"], quote=True)}">\n'
                    f'<link rel="icon" type="image/png" sizes="32x32" href="{self.icon_url(32)}">\n'
                    f'<link rel="apple-touch-icon" sizes="180x180" href="{self.icon_url(180)}">\n'
                    '<link rel="manifest" href="/manifest.webmanifest">\n')
        return head + metadata + closing + body