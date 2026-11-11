"""Reference deltas: a shared ZIP library, independent of preference selection."""
from __future__ import annotations

import base64
from collections import OrderedDict
from contextlib import contextmanager
import hashlib
from importlib.resources import files
import os
from pathlib import Path
import re
import stat
import threading
import uuid

from . import appearance_packages as packages
from .errors import AppError

LIMIT = packages.CSS_LIMIT
PACKAGE_SUMMARY_CACHE_LIMIT = 128
_PACKAGE_SUMMARY_LOCK = threading.Lock()
BUILTINS = {
    'default': {'name': 'Amplifier', 'description': 'Blue and lilac, over Reference.'},
    'aurora': {'name': 'Aurora', 'description': 'Luminous color and atmospheric materials.'},
    'atelier': {'name': 'Atelier', 'description': 'Warm paper and editorial typography.'},
    'graphite': {'name': 'Graphite', 'description': 'Precise edges and technical typography.'},
    'digital-rain': {'name': 'Digital Rain', 'description': 'Optional emerald code-rain artwork.'},
    'lightline': {'name': 'Lightline', 'description': 'Architectural light paths and quiet surfaces.'},
    'ready': {'name': 'Breadbin', 'description': 'Ivory casing and a violet conversation screen.'},
}


def definitions(schema, string):
    content = {'name': string(100), 'css': string(LIMIT), 'description': string(240)}
    target = {'id': string(240), 'expectedFingerprint': {'type': 'string', 'pattern': '^[a-f0-9]{64}$'}}
    return {
        'theme.list': ('List bundled and saved Reference deltas without selecting them.', schema()),
        'theme.read': ('Read an exact library delta, metadata and saved fingerprint.', schema({'id': string(240)}, ['id'])),
        'theme.save': ('Validate and save a named Reference delta as ZIP; does not select it.', schema(content, ['name', 'css'])),
        'theme.import': ('Validate a version-2 Reference ZIP before adding it to the shared library; never selects it.',
                         schema({'packageBase64': string(packages.PACKAGE_BASE64_LIMIT), 'name': string(100), 'description': string(240)}, ['packageBase64'])),
        'theme.update': ('Update a saved ZIP at its exact fingerprint, preserving declared assets and notices.',
                         schema({**content, **target}, ['id', 'expectedFingerprint', 'name', 'css'])),
        'theme.delete': ('Delete an exact saved ZIP; preference selection is not changed.', schema(target, ['id', 'expectedFingerprint'])),
        'theme.export': ('Export a saved or bundled library ID as ZIP; never exports a preview or draft.', schema({'id': string(240)}, ['id'])),
    }


def fingerprint(css):
    return hashlib.sha256(css.encode('utf-8')).hexdigest()


def preset(service, identity):
    if identity not in BUILTINS:
        raise AppError('Appearance not found.', 404)
    css = files('amplifier_web').joinpath('appearances', identity + '.amplifier.css').read_text(encoding='utf-8')
    from .service import validate_theme
    validate_theme(css)
    return {'id': 'builtin:' + identity, **BUILTINS[identity], 'source': 'built-in', 'base': 'reference-v1',
            'css': css, 'fingerprint': fingerprint(css), 'readOnly': True}


def folder(service):
    return service.data_dir / 'themes'


@contextmanager
def directory(service, *, create=False, writing=False):
    root = folder(service)
    if create:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except FileNotFoundError:
        raise AppError('Appearance not found.', 404) from None
    except OSError as exc:
        raise AppError('The appearance library must be a real directory, not a link.') from exc
    try:
        if create or writing:
            import fcntl
            fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield descriptor
    finally:
        os.close(descriptor)


def read_package_bytes(path, descriptor):
    try:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
    except OSError as exc:
        raise AppError('Appearance file is unavailable; links are not supported.', 404) from exc
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > packages.PACKAGE_LIMIT:
            raise AppError('This appearance is unavailable or exceeds the 4 MB raw ZIP limit.')
        raw = stream.read(packages.PACKAGE_LIMIT + 1)
    if len(raw) > packages.PACKAGE_LIMIT:
        raise AppError('This appearance exceeds the 4 MB raw ZIP limit.')
    return raw


def read_package(path, descriptor):
    raw = read_package_bytes(path, descriptor)
    return raw, packages.load(raw)


def package_metadata(package):
    return {key: package.manifest[key] for key in ('name', 'description', 'author') if key in package.manifest} | {
        'source': 'saved', **package.summary}


def package_row(path, raw, package):
    return {'id': 'saved:' + path.name, **package_metadata(package), 'css': package.css,
            'fingerprint': hashlib.sha256(raw).hexdigest()}


def package_listing_row(service, path, descriptor):
    # Exact bounded ZIP reads/hashes witness invalidation; only small validated
    # summaries are cached. Full reads always revalidate assets and delta CSS.
    raw = read_package_bytes(path, descriptor)
    digest = hashlib.sha256(raw).hexdigest()
    with _PACKAGE_SUMMARY_LOCK:
        cache = getattr(service, '_appearance_package_summaries', None)
        if cache is None:
            cache = service._appearance_package_summaries = OrderedDict()
        cached = cache.get(digest)
        if cached is not None:
            cache.move_to_end(digest)
            return {'id': 'saved:' + path.name, 'fingerprint': digest, **cached}
    value = package_metadata(packages.load(raw))
    with _PACKAGE_SUMMARY_LOCK:
        cache[digest] = value
        cache.move_to_end(digest)
        while len(cache) > PACKAGE_SUMMARY_CACHE_LIMIT:
            cache.popitem(last=False)
    return {'id': 'saved:' + path.name, 'fingerprint': digest, **value}


def saved_name(identity):
    name = identity.removeprefix('saved:')
    if (not identity.startswith('saved:') or Path(name).name != name or not name.endswith('.zip')
            or any(char in name for char in '/\\\0')):
        raise AppError('Choose a saved ZIP appearance from the library.', 404)
    return name


def read(service, identity):
    if identity.startswith('builtin:'):
        return preset(service, identity[8:])
    name = saved_name(identity)
    try:
        with directory(service) as descriptor:
            raw, package = read_package(Path(name), descriptor)
            return package_row(Path(name), raw, package)
    except (OSError, UnicodeError) as exc:
        raise AppError('Appearance not found.', 404) from exc


def resolve(service, identity):
    """Selected presentation only: exact-byte invalidation, at most eight deltas.

    Publication never walks the library or history. Each resolution still reads
    and hashes the selected bounded file; unchanged bytes avoid decompression and
    validation. Explicit theme.read always revalidates the complete package.
    """
    if identity.startswith('builtin:'):
        key = identity[8:]
        if key not in BUILTINS:
            raise AppError('Appearance not found.', 404)
        css = files('amplifier_web').joinpath('appearances', key + '.amplifier.css').read_text(encoding='utf-8')
        digest = fingerprint(css)
        raw = None
    else:
        name = saved_name(identity)
        with directory(service) as descriptor:
            raw = read_package_bytes(Path(name), descriptor)
        digest = hashlib.sha256(raw).hexdigest()
    cache = getattr(service, '_resolved_appearances', None)
    if cache is None:
        cache = service._resolved_appearances = OrderedDict()
    key = (identity, digest)
    if key not in cache:
        if raw is None:
            from .service import validate_theme
            validate_theme(css)
            row = {'id': identity, **BUILTINS[identity[8:]], 'source': 'built-in',
                   'base': 'reference-v1', 'css': css, 'fingerprint': digest, 'readOnly': True}
        else:
            row = package_row(Path(name), raw, packages.load(raw))
        cache[key] = row
        while len(cache) > 8:
            cache.popitem(last=False)
    cache.move_to_end(key)
    return dict(cache[key])


def listing(service):
    rows, errors = [], []
    for key in BUILTINS:
        try:
            rows.append(preset(service, key))
        except (OSError, AppError) as exc:
            errors.append({'name': 'builtin:' + key, 'message': str(exc)})
    root = folder(service)
    if root.exists() or root.is_symlink():
        with directory(service) as descriptor:
            for name in sorted(os.listdir(descriptor), key=str.lower):
                if not name.endswith('.zip'):
                    continue
                try:
                    rows.append(package_listing_row(service, Path(name), descriptor))
                except (OSError, UnicodeError, AppError) as exc:
                    errors.append({'name': name, 'message': str(exc)})
    return {'items': [{key: value for key, value in row.items() if key != 'css'} for row in rows],
            'errors': errors, 'directory': str(root)}


def slug(name):
    return re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')[:60] or 'appearance'


def publish(descriptor, name, content, *, replace=False):
    temporary = '.' + uuid.uuid4().hex + '.tmp'
    fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600, dir_fd=descriptor)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if replace:
            os.replace(temporary, name, src_dir_fd=descriptor, dst_dir_fd=descriptor)
        else:
            try:
                os.link(temporary, name, src_dir_fd=descriptor, dst_dir_fd=descriptor, follow_symlinks=False)
            except FileExistsError:
                if read_package_bytes(Path(name), descriptor) != content:
                    raise AppError('A different appearance already uses this file name.') from None
        os.fsync(descriptor)
    finally:
        try:
            os.unlink(temporary, dir_fd=descriptor)
        except FileNotFoundError:
            pass


def summary(service, row):
    return {key: value for key, value in row.items() if key != 'css'} | {'directory': str(folder(service))}


def import_name(descriptor, name, content):
    # In-place edits keep identity, so the original content filename can be
    # occupied by different bytes. Reuse identical imports even beyond a gap.
    stem = name.removesuffix('.zip')
    candidates = {name if index == 1 else f'{stem}-{index}.zip': index for index in range(1, 1001)}
    occupied = set()
    with os.scandir(descriptor) as entries:
        for entry in entries:
            if (index := candidates.get(entry.name)) is not None:
                occupied.add(index)
    for index in sorted(occupied):
        candidate = name if index == 1 else f'{stem}-{index}.zip'
        if read_package_bytes(Path(candidate), descriptor) == content:
            return candidate
    for index in range(1, 1001):
        if index not in occupied:
            return name if index == 1 else f'{stem}-{index}.zip'
    raise AppError('Too many appearances share this import identity. Choose another name.')


def save_package(service, package):
    content = packages.encode(package.files)
    filename = f"{slug(package.manifest['name'])}-{hashlib.sha256(content).hexdigest()[:16]}.zip"
    with directory(service, create=True) as descriptor:
        filename = import_name(descriptor, filename, content)
        publish(descriptor, filename, content)
        return summary(service, package_row(Path(filename), content, package))


def save(service, args):
    return save_package(service, packages.from_css(args['css'], args['name'], args.get('description')))


def import_appearance(service, args):
    if 'css' in args or 'packageBase64' not in args:
        raise AppError('Import a version-2 ZIP using packageBase64.')
    package = packages.load(packages.decode_input(args['packageBase64']))
    return save_package(service, packages.with_metadata(package, args.get('name'), args.get('description')))


def mutate(service, args, *, deleting=False):
    filename = saved_name(args['id'])
    with directory(service, writing=True) as descriptor:
        try:
            raw, package = read_package(Path(filename), descriptor)
        except (OSError, UnicodeError) as exc:
            raise AppError('Appearance not found.', 404) from exc
        if hashlib.sha256(raw).hexdigest() != args['expectedFingerprint']:
            raise AppError('This appearance changed. Read it again before updating or deleting it.', 409)
        if deleting:
            os.unlink(filename, dir_fd=descriptor)
            os.fsync(descriptor)
            return {'id': args['id'], 'deleted': True, 'preferencesUnchanged': True}
        updated = packages.with_metadata(package, args['name'], args.get('description', package.manifest.get('description', '')), args['css'])
        content = packages.encode(updated.files)
        publish(descriptor, filename, content, replace=True)
        return summary(service, package_row(Path(filename), content, updated))


def export(service, identity):
    if identity.startswith('saved:'):
        with directory(service) as descriptor:
            raw, package = read_package(Path(saved_name(identity)), descriptor)
        name = package.manifest['name']
    else:
        row = read(service, identity)
        name = row['name']
        raw = packages.encode(packages.from_css(row['css'], name, row.get('description')).files)
    return {'id': identity, 'name': name, 'format': 'zip', 'filename': slug(name) + '.zip',
            'mimeType': 'application/zip', 'base64': base64.b64encode(raw).decode('ascii'),
            'fingerprint': hashlib.sha256(raw).hexdigest()}