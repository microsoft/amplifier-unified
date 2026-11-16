"""Listener-owned PWA presentation. Never a workspace or install identity."""
from __future__ import annotations

import base64
import binascii
import colorsys
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
import unicodedata
import warnings

from .deployment import canonical_host, write_private

MAX_IMAGE_BYTES = 2 * 1024 * 1024
MAX_IMAGE_TEXT = 4 * ((MAX_IMAGE_BYTES + 2) // 3)
SIZES = (32, 180, 192, 512)
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
            'An empty name uses the default; image is raw base64 PNG, JPEG, WebP, GIF or ICO '
            '(up to 2 MB and 4096 × 4096). An empty image returns to the colored badge. '
            'Requires global host-defaults authority.',
            schema({'patch': schema({
                'name': string(120),
                'color': {'type': 'string', 'pattern': '^#[0-9a-fA-F]{6}$'},
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
        seed = hashlib.sha256((hostname + key).encode()).digest()
        rgb = colorsys.hls_to_rgb(int.from_bytes(seed[:2], 'big') / 65536, .40, .62)
        self.default_color = '#' + ''.join(f'{round(channel * 255):02x}' for channel in rgb)
        self._icons = {}
        self._record = self._load()
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
        revision = hashlib.sha256(json.dumps([name, color, self._record['image']],
                                            ensure_ascii=True).encode()).hexdigest()[:24]
        self._snapshot = {
            'name': name, 'defaultName': self.default_name,
            'color': color, 'defaultColor': self.default_color,
            'iconUrl': f'/instance-icon/192.png?v={revision}',
            'customIcon': bool(self._record['image']), 'revision': revision,
        }
        self._current = (self._record, self._snapshot)
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
            result['color'] = _color(patch['color'])
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

    def icon_url(self, size):
        return f'/instance-icon/{size}.png?v={self._snapshot["revision"]}'

    def icon(self, size):
        from PIL import Image, ImageOps
        if size not in SIZES:
            raise ValueError('Choose an app icon size of 32, 180, 192 or 512.')
        # Requests encode off the event loop. A preference change during that
        # encode must not put old pixels into the new revision's cache.
        record, snapshot = self._current
        key = (snapshot['revision'], size)
        cached = self._icons.get(key)
        if cached is None:
            if record['image']:
                with Image.open(io.BytesIO(base64.b64decode(record['image']))) as source:
                    image = ImageOps.pad(source.convert('RGBA'), (size, size),
                                         method=Image.Resampling.LANCZOS, color=(0, 0, 0, 0))
            else:
                image = Image.new('RGBA', (512, 512), snapshot['color'])
                # Keep the shipped Amplifier mark recognizable, with enough
                # surrounding instance color to distinguish installed apps.
                mark_path = Path(__file__).parent / 'static/branding/pwa/pwa-512.png'
                with Image.open(mark_path) as source:
                    mark = source.convert('RGBA')
                    mark.thumbnail((384, 384), Image.Resampling.LANCZOS)
                    image.alpha_composite(mark, ((512 - mark.width) // 2, (512 - mark.height) // 2))
                image = image.resize((size, size), Image.Resampling.LANCZOS)
            cached = _png(image)
            if self._current[1]['revision'] == snapshot['revision']:
                self._icons[key] = cached
        return cached

    def manifest(self, template):
        result = {**template, 'name': self._snapshot['name'], 'short_name': self._snapshot['name']}
        result['icons'] = [{**icon, 'src': self.icon_url(int(icon['sizes'].split('x')[0]))}
                           if icon.get('sizes') in {'192x192', '512x512'} else dict(icon)
                           for icon in template['icons']]
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