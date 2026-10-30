"""ZIP boundary and library lifecycle checks; no network, renderer or model calls."""
import base64
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import struct
import zipfile
import zlib

import pytest

from amplifier_web import appearance_packages as packages, theme_library
from amplifier_web.service import AppError, AppService, validate_theme
from test_canvas_apps import app, dispatch


PNG = base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl6'
    'AAAAAElFTkSuQmCC')
LICENSE = 'Example font license\nCopyright Example Contributors\nPermission is granted for this test fixture.\n'
# Signature/length fixtures test admission, not browser font decoding.
WOFF = b'wOFF' + b'\0\1\0\0' + struct.pack('>I', 44) + b'\0' * 32
WOFF2 = b'wOF2' + b'\0\1\0\0' + struct.pack('>I', 48) + b'\0' * 36
MANIFEST = {
    'type': 'appearance', 'formatVersion': 1, 'name': 'Paper',
    'description': 'A portable look', 'author': 'Example Contributors', 'stylesheet': 'theme.css',
    'assets': [
        {'path': 'assets/paper.png', 'mediaType': 'image/png'},
        {'path': 'assets/body.woff', 'mediaType': 'font/woff', 'license': 'licenses/OFL.txt'},
    ],
    'licenses': ['licenses/OFL.txt'],
}
CSS = ('@font-face{font-family:Example;src:url("assets/body.woff") format("woff")}'
       '#amp-one{background-image:url(assets/paper.png);color:#123456}')


def contents(css=CSS, manifest=None):
    return {
        'appearance.json': json.dumps(MANIFEST if manifest is None else manifest).encode(),
        'theme.css': css.encode(),
        'assets/paper.png': PNG,
        'assets/body.woff': WOFF,
        'licenses/OFL.txt': LICENSE.encode(),
    }


def raw_zip(files=None, *, compression=zipfile.ZIP_STORED, comment=b''):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', compression=compression) as archive:
        for name, body in (contents() if files is None else files).items():
            archive.writestr(name, body)
        archive.comment = comment
    return stream.getvalue()


def encoded(raw=None):
    return base64.b64encode(raw_zip() if raw is None else raw).decode()


def manifest_files(patch):
    manifest = deepcopy(MANIFEST)
    manifest.update(patch)
    return contents(manifest=manifest)


def changed_zip_field(raw, central_offset, local_offset, value, fmt='<H'):
    raw = bytearray(raw)
    struct.pack_into(fmt, raw, raw.index(b'PK\x01\x02') + central_offset, value)
    struct.pack_into(fmt, raw, raw.index(b'PK\x03\x04') + local_offset, value)
    return bytes(raw)


def member_records(raw):
    """Record offsets for byte-patching real, otherwise ordinary ZIP fixtures."""
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        central = archive.start_dir
        result = {}
        for member in archive.infolist():
            name_size, extra_size, comment_size = struct.unpack_from('<3H', raw, central + 28)
            start = member.header_offset + 30 + sum(struct.unpack_from('<2H', raw, member.header_offset + 26))
            result[member.filename] = (member, central, start, start + member.compress_size)
            central += 46 + name_size + extra_size + comment_size
        return result


def patch_member(raw, name, *, size=None, compressed=None, crc=None):
    member, central, _, _ = member_records(raw)[name]
    result = bytearray(raw)
    for value, local_field, central_field in ((size, 22, 24), (compressed, 18, 20), (crc, 14, 16)):
        if value is not None:
            struct.pack_into('<I', result, member.header_offset + local_field, value)
            struct.pack_into('<I', result, central + central_field, value)
    return bytes(result)


def replace_member_payload(raw, name, payload):
    """Patch compressed bytes AND relocate later records/EOCD, avoiding gap oracles."""
    records = member_records(raw)
    member, _, start, end = records[name]
    delta = len(payload) - (end - start)
    result = bytearray(raw[:start] + payload + raw[end:])
    for other, central, _, _ in records.values():
        struct.pack_into('<I', result, central + delta + 42,
                         other.header_offset + (delta if other.header_offset >= end else 0))
    eocd = raw.rindex(b'PK\x05\x06')
    central_start = struct.unpack_from('<I', raw, eocd + 16)[0]
    struct.pack_into('<I', result, eocd + delta + 16, central_start + delta)
    return patch_member(bytes(result), name, compressed=len(payload))


async def imported(app, **args):
    return (await dispatch(app, 'theme.import', {'packageBase64': encoded(), **args}))['result']


async def read(app, row):
    return (await dispatch(app, 'theme.read', {'id': row['id']}))['result']


async def exported(app, row=None, format='zip'):
    return await dispatch(app, 'theme.export', {'format': format, **({'id': row['id']} if row else {})})


def test_manifest_compiles_assets_and_full_attribution_without_extraction():
    package = packages.load(raw_zip())
    assert package.summary == {'format': 'zip', 'packageVersion': 1, 'assetCount': 2, 'licenseCount': 1}
    meta, body = theme_library.metadata(package.css)
    assert meta['name'] == 'Paper' and meta['author'] == MANIFEST['author']
    assert meta[packages.NOTICE_KEY] == {'licenses/OFL.txt': LICENSE}
    assert f'data:image/png;base64,{base64.b64encode(PNG).decode()}' in body
    assert f'data:font/woff;base64,{base64.b64encode(WOFF).decode()}' in body
    assert 'url(assets/' not in body and 'url("assets/' not in body
    assert len(package.css.encode()) <= packages.CSS_LIMIT


@pytest.mark.parametrize('css', [
    '#amp-one{background:url(assets/paper.png)}',
    '#amp-one{background:url("assets/paper.png")}',
    "#amp-one{background:url('assets/paper.png')}",
    r'#amp-one{background:u\72l("assets/paper.png")}',
    r'#amp-one{background:url("assets/pap\65 r.png")}',
    '#amp-one{background:image-set("assets/paper.png" 1x,url(assets/paper.png) 2x)}',
    '#amp-one{background:-webkit-image-set("assets/paper.png" 1x)}',
    '#amp-one{background:image("assets/paper.png")}',
    '@font-face{src:src("assets/body.woff")}',
    '@media(min-width:10px){#amp-one{background:url(assets/paper.png)}}',
])
def test_ast_rewrites_url_tokens_and_url_bearing_strings(css):
    compiled = packages.load(raw_zip(contents(css))).css
    assert 'data:image/png;base64,' in compiled or 'data:font/woff;base64,' in compiled
    assert 'assets/paper.png' not in compiled and 'assets/body.woff' not in compiled


def test_relative_reference_resolution_and_comments_are_not_regex_replaced():
    manifest = {**MANIFEST, 'stylesheet': 'styles/main.css'}
    files = contents('/* url(assets/paper.png) is a comment */'
                     '#amp-one{background:url("../assets/paper.png")}', manifest)
    files['styles/main.css'] = files.pop('theme.css')
    package = packages.load(raw_zip(files))
    assert '/* url(assets/paper.png) is a comment */' in package.css
    assert 'url("data:image/png;base64,' in package.css


@pytest.mark.parametrize('css', [
    '@import "assets/paper.png";',
    '@import url("https://example.invalid/style.css");',
    r'@\69mport "assets/paper.png";',
    '@namespace url("assets/paper.png");',
    '#amp-one{background:url(https://example.invalid/paper.png)}',
    '#amp-one{background:url("//example.invalid/paper.png")}',
    '#amp-one{background:url("/assets/paper.png")}',
    '#amp-one{background:url("file:///assets/paper.png")}',
    '#amp-one{background:url("javascript:alert(1)")}',
    '#amp-one{background:url("data:image/svg+xml;base64,PHN2Zz4=")}',
    '#amp-one{background:url("data:text/html;base64,PGgxPg==")}',
    '#amp-one{background:url("data:image/png;base64,SGVsbG8=")}',
    '#amp-one{background:url("assets/missing.png")}',
    '#amp-one{background:url("assets/paper.png?secret")}',
    '#amp-one{background:url("assets/paper%2epng")}',
    '#amp-one{background:url("../../assets/paper.png")}',
    '#amp-one{background:url(var(--asset))}',
    '#amp-one{background:image-set("https://example.invalid/paper.png" 1x)}',
    '#amp-one{--asset:"https://example.invalid/paper.png";background:image-set(var(--asset) 1x)}',
    '#amp-one{background:image-set(attr(data-src) 1x)}',
    '#amp-one{background:image("https://example.invalid/paper.png")}',
    '#amp-one{background:expression(alert(1))}',
    '#amp-one{background:paint(custom)}',
    '</style><script>alert(1)</script>',
    '#amp-one{background:url("unterminated)}',
])
def test_unsafe_or_unresolvable_css_is_rejected(css):
    with pytest.raises(AppError):
        packages.load(raw_zip(contents(css)))


def test_valid_embedded_assets_remain_embedded():
    css = '#amp-one{background:url("data:image/png;base64,' + base64.b64encode(PNG).decode() + '")}'
    assert 'data:image/png;base64,' in packages.load(raw_zip(contents(css))).css


@pytest.mark.parametrize('function', ['image-set', '-webkit-image-set', 'image', 'src'])
@pytest.mark.parametrize('substitution', [
    'if(style(--probe:yes):"/__appearance_probe.png";else:"/__appearance_other.png")',
    'future-substitute("/__appearance_probe.png")',
    'type(if(style(--probe:yes):"/__appearance_probe.png";else:"image/png"))',
    'var(--probe)', 'env(probe)', 'attr(data-probe)',
])
def test_plain_css_and_package_reject_nested_and_unknown_image_substitutions(function, substitution):
    css = f'#amp-one{{background:{function}({substitution})}}'
    with pytest.raises(AppError, match='literal assets'):
        validate_theme(css)
    with pytest.raises(AppError, match='literal assets'):
        packages.load(raw_zip(contents(css)))


@pytest.mark.parametrize('function', ['image-set', '-webkit-image-set', 'image', 'src'])
def test_image_functions_allow_literal_local_and_embedded_assets(function):
    local = f'#amp-one{{background:{function}("assets/paper.png")}}'
    compiled = packages.load(raw_zip(contents(local))).css
    validate_theme(compiled)
    embedded = f'data:image/png;base64,{base64.b64encode(PNG).decode()}'
    plain = f'#amp-one{{background:{function}("{embedded}")}}'
    validate_theme(plain)
    assert embedded in packages.load(raw_zip(contents(plain))).css


def test_image_set_literal_nested_images_annotations_gradients_and_density_remain_valid():
    css = ('#amp-one{background:image-set(image("assets/paper.png") 1x type("image/png"),'
           'url("assets/paper.png") calc(2 * 1dppx),linear-gradient(rgb(0 0 0),white) 3x)}')
    compiled = packages.load(raw_zip(contents(css))).css
    validate_theme(compiled)
    assert 'type("image/png")' in compiled


def test_notice_comment_and_style_terminators_remain_text_and_roundtrip():
    manifest = {**MANIFEST, 'author': 'Example */ </style> author'}
    files = contents(manifest=manifest)
    notice = LICENSE + 'A literal comment terminator */ and </style> in a notice.\n'
    files['licenses/OFL.txt'] = notice.encode()
    css = packages.load(raw_zip(files)).css
    meta, _ = theme_library.metadata(css)
    assert meta['author'] == manifest['author']
    assert meta[packages.NOTICE_KEY]['licenses/OFL.txt'] == notice
    assert '</style' not in css.lower()
    assert packages.from_css(css, 'Copy').files['licenses/OFL.txt'].decode() == notice


@pytest.mark.parametrize('css', [
    '', ' \n\t',
    '#amp-one{color:' + '(' * 70 + 'red' + ')' * 70 + '}',
    '#amp-one{' + '--large:a;' * 100_001 + '}',
])
def test_empty_deep_or_oversized_stylesheet_is_rejected(css):
    with pytest.raises(AppError):
        packages.load(raw_zip(contents(css)))


@pytest.mark.parametrize('patch', [
    {'type': 'bundle'}, {'formatVersion': 2}, {'formatVersion': True}, {'formatVersion': '1'},
    {'name': []}, {'name': ''}, {'name': 'a' * 101}, {'author': {}}, {'author': '\ud800'},
    {'description': []}, {'stylesheet': '../theme.css'}, {'stylesheet': 'theme.js'},
    {'assets': {}}, {'licenses': 'licenses/OFL.txt'},
    {'layout': {}}, {'permissions': []}, {'scripts': ['run.js']}, {'preview': 'preview.png'},
    {'assets': [{'path': 'assets/body.woff', 'mediaType': 'font/woff'}]},
    {'assets': [{'path': 'assets/body.woff', 'mediaType': 'font/woff', 'license': 'licenses/missing.txt'}]},
    {'assets': [{'path': 'assets/body.ttf', 'mediaType': 'font/ttf', 'license': 'licenses/OFL.txt'}]},
    {'assets': [{'path': 'assets/run.zip', 'mediaType': 'image/png'}]},
    {'assets': [{'path': 'assets/run.html', 'mediaType': 'text/html'}]},
    {'assets': [{'path': 'assets/paper.png', 'mediaType': 'image/png', 'executable': True}]},
    {'licenses': ['licenses/OFL.txt', 'licenses/ofl.txt']},
    {'licenses': ['licenses/OFL.txt', '../OFL.txt']},
])
def test_strict_manifest_types_fields_versions_and_font_license(patch):
    with pytest.raises(AppError):
        packages.load(raw_zip(manifest_files(patch)))


@pytest.mark.parametrize('manifest', [
    b'[]', b'null', b'not JSON', b'{"type":"appearance","type":"bundle"}',
    b'\xef\xbb\xbf{"type":"appearance"}', b'{"type":NaN}',
    b'{"assets":[{"path":"one","path":"two"}]}',
])
def test_malformed_json_bom_and_duplicate_keys_are_rejected(manifest):
    files = contents()
    files['appearance.json'] = manifest
    with pytest.raises(AppError):
        packages.load(raw_zip(files))


@pytest.mark.parametrize('path', [
    '../outside.txt', '/outside.txt', r'assets\outside.png', 'C:/outside.txt',
    './theme.css', 'a//b', 'a/../b', 'assets/e\u0301.png', 'assets/%70aper.png',
    'theme.css/', 'unrelated/', 'nested.zip', 'preview.png', 'run.js', 'licenses/run.html',
])
def test_traversal_aliases_directories_nested_archives_and_unlisted_members(path):
    with pytest.raises(AppError):
        packages.load(raw_zip({**contents(), path: b''}))


def test_optional_declared_directory_records_are_accepted():
    files = {**contents(), 'assets/': b'', 'licenses/': b''}
    assert packages.load(raw_zip(files)).manifest['name'] == 'Paper'


def test_duplicate_names_case_aliases_and_nul_are_rejected():
    for alias in ('Theme.css', 'THEME.CSS'):
        with pytest.raises(AppError):
            packages.load(raw_zip({**contents(), alias: b'ignored'}))
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as archive:
        archive.writestr('theme.css', b'one')
        with pytest.warns(UserWarning, match='Duplicate'):
            archive.writestr('theme.css', b'two')
    with pytest.raises(AppError):
        packages.load(stream.getvalue())
    raw = raw_zip({**contents(), 'evilX.css': b'ignored'}).replace(b'evilX.css', b'evil\0.css')
    with pytest.raises(AppError):
        packages.load(raw)


@pytest.mark.parametrize('mode', [stat.S_IFLNK, stat.S_IFIFO, stat.S_IFSOCK, stat.S_IFCHR, stat.S_IFBLK])
def test_archive_links_and_special_files_are_rejected(mode):
    entry = zipfile.ZipInfo('assets/link.png')
    entry.external_attr = (mode | 0o600) << 16
    with pytest.raises(AppError):
        packages.load(raw_zip({**contents(), entry: b'assets/paper.png'}))


@pytest.mark.parametrize('media,path,data', [
    ('image/png', 'assets/image.png', PNG),
    ('image/jpeg', 'assets/image.jpeg', b'\xff\xd8\xff\xe0\xff\xd9'),
    ('image/webp', 'assets/image.webp', b'RIFF' + struct.pack('<I', 8) + b'WEBPVP8 '),
    ('font/woff', 'assets/font.woff', WOFF),
    ('font/woff2', 'assets/font.woff2', WOFF2),
])
def test_allowed_type_extension_and_signature(media, path, data):
    manifest = {'type': 'appearance', 'formatVersion': 1, 'name': 'Signatures', 'stylesheet': 'theme.css',
                'assets': [{'path': path, 'mediaType': media, 'license': 'licenses/test.txt'}],
                'licenses': ['licenses/test.txt']}
    files = {'appearance.json': json.dumps(manifest).encode(), 'theme.css': b'#amp-one{color:red}',
             path: data, 'licenses/test.txt': LICENSE.encode()}
    assert packages.load(raw_zip(files)).summary['assetCount'] == 1
    files[path] = b'<script>not the declared format</script>'
    with pytest.raises(AppError, match='signature'):
        packages.load(raw_zip(files))


def test_unrelated_zip_missing_manifest_or_files_and_utf8_fail_closed():
    for files in (
        {'notes.txt': b'not an appearance'},
        {name: data for name, data in contents().items() if name != 'assets/paper.png'},
        {**contents(), 'theme.css': b'\xff'},
        {**contents(), 'licenses/OFL.txt': b'\xff'},
        {**contents(), 'licenses/OFL.txt': b'not text\0'},
    ):
        with pytest.raises(AppError):
            packages.load(raw_zip(files))
    with pytest.raises(AppError):
        packages.load(b'not a ZIP')


def test_encryption_unsupported_compression_and_crc_failure():
    raw = raw_zip()
    for tampered in (changed_zip_field(raw, 8, 6, 1), changed_zip_field(raw, 10, 8, 99)):
        with pytest.raises(AppError):
            packages.load(tampered)
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        member = archive.getinfo('theme.css')
        offset = member.header_offset + 30 + len(member.filename.encode()) + len(member.extra)
    tampered = bytearray(raw)
    tampered[offset] ^= 1
    with pytest.raises(AppError, match='checksum'):
        packages.load(bytes(tampered))


def test_all_advertised_bounds_are_checked_before_reading_members(monkeypatch):
    too_many = {f'{index}.txt': b'' for index in range(packages.MEMBER_COUNT + 1)}
    oversized = {**contents(), 'oversized.txt': b'x' * (packages.MEMBER_LIMIT + 1)}
    ratio = {**contents(), 'ratio.txt': b'x' * 1_500_000}
    # Repetition shorter than the DEFLATE window gives moderate compression,
    # exceeding the total bound without exceeding the ratio or encoded bound.
    block = os.urandom(20_000) * 90
    total = {**contents(), **{f'assets/{i}.png': block for i in range(5)}}
    cases = [raw_zip(too_many), raw_zip(oversized), raw_zip(ratio, compression=zipfile.ZIP_DEFLATED),
             raw_zip(total, compression=zipfile.ZIP_DEFLATED)]
    opened = []
    def unexpected_open(*args, **kwargs):
        opened.append(True)
        raise AssertionError('A member was opened before archive preflight completed')
    monkeypatch.setattr(packages, 'decode_member', unexpected_open)
    for raw in cases:
        with pytest.raises(AppError):
            packages.load(raw)
    assert not opened


@pytest.mark.parametrize('compression', [zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED])
def test_real_understated_expanded_length_with_matching_prefix_crc_is_rejected(compression):
    prefix = b'#amp-one{color:red}'
    files = contents()
    files['theme.css'] = prefix + os.urandom(40_000)
    raw = raw_zip(files, compression=compression)
    # ZipExtFile would clip output to this prefix and accept its matching CRC.
    patched = patch_member(raw, 'theme.css', size=len(prefix), crc=zlib.crc32(prefix))
    member, _, start, end = member_records(patched)['theme.css']
    actual = zlib.decompress(patched[start:end], -15) if compression == zipfile.ZIP_DEFLATED else patched[start:end]
    assert actual == files['theme.css'] and len(actual) > member.file_size
    assert member.CRC == zlib.crc32(prefix)
    with pytest.raises(AppError):
        packages.load(patched)


def test_stored_understated_both_lengths_cannot_hide_remaining_payload_as_gap():
    prefix = b'#amp-one{color:red}'
    files = contents()
    files['theme.css'] = prefix + b'Hidden payload'
    raw = raw_zip(files)
    patched = patch_member(raw, 'theme.css', size=len(prefix), compressed=len(prefix), crc=zlib.crc32(prefix))
    with pytest.raises(AppError, match='gap'):
        packages.load(patched)


@pytest.mark.parametrize('damage', ['truncated', 'trailing'])
def test_real_deflate_requires_end_of_stream_and_no_trailing_compressed_data(damage):
    raw = raw_zip(compression=zipfile.ZIP_DEFLATED)
    _, _, start, end = member_records(raw)['theme.css']
    body = contents()['theme.css']
    if damage == 'truncated':
        # Complete nonfinal STORED block inside DEFLATE, followed by an empty
        # final block. Dropping only the final block preserves ALL body bytes
        # and CRC while removing end-of-stream -- ZipExtFile accepts that prefix.
        payload = b'\x00' + struct.pack('<HH', len(body), 0xffff - len(body)) + body
        complete = replace_member_payload(raw, 'theme.css', payload + b'\x01\x00\x00\xff\xff')
        assert packages.load(complete).manifest['name'] == 'Paper'
        patched = replace_member_payload(complete, 'theme.css', payload)
    else:
        patched = replace_member_payload(raw, 'theme.css', raw[start:end] + b'hidden')
    # Relocate central records so rejection must be from actual DEFLATE decoding,
    # not a mismatched header, leftover gap, or a clipped ZipExtFile read.
    _, _, start, end = member_records(patched)['theme.css']
    oracle = zlib.decompressobj(-15)
    assert oracle.decompress(patched[start:end]) == body
    if damage == 'truncated':
        assert not oracle.eof
    else:
        assert oracle.unused_data == b'hidden'
    with pytest.raises(AppError, match='DEFLATE stream'):
        packages.load(patched)


@pytest.mark.parametrize('compression', [zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED])
@pytest.mark.parametrize('signed', [False, True])
def test_zip32_data_descriptors_are_independently_validated(compression, signed):
    raw = raw_zip(compression=compression)
    member, _, start, end = member_records(raw)['theme.css']
    descriptor = (b'PK\x07\x08' if signed else b'') + struct.pack('<3I', member.CRC, member.compress_size, member.file_size)
    patched = replace_member_payload(raw, 'theme.css', raw[start:end] + descriptor)
    patched = patch_member(patched, 'theme.css', compressed=member.compress_size)
    local, central, _, _ = member_records(patched)['theme.css']
    result = bytearray(patched)
    struct.pack_into('<H', result, local.header_offset + 6, local.flag_bits | 8)
    struct.pack_into('<H', result, central + 8, local.flag_bits | 8)
    struct.pack_into('<3I', result, local.header_offset + 14, 0, 0, 0)
    assert packages.load(bytes(result)).manifest['name'] == 'Paper'
    result[end + (4 if signed else 0)] ^= 1
    with pytest.raises(AppError, match='descriptor'):
        packages.load(bytes(result))


@pytest.mark.parametrize('field,fmt,value', [(6, '<H', 8), (8, '<H', 8), (14, '<I', 0), (22, '<I', 0)])
def test_local_header_must_match_central_flags_method_crc_and_size(field, fmt, value):
    raw = raw_zip()
    member, _, _, _ = member_records(raw)['theme.css']
    patched = bytearray(raw)
    struct.pack_into(fmt, patched, member.header_offset + field, value)
    with pytest.raises(AppError, match='local ZIP'):
        packages.load(bytes(patched))


def test_local_name_mismatch_overlapping_member_and_truncated_local_header_are_rejected():
    raw = raw_zip()
    member, central, _, _ = member_records(raw)['theme.css']
    for damage in ('name', 'overlap', 'truncated'):
        patched = bytearray(raw)
        if damage == 'name':
            patched[member.header_offset + 30] ^= 1
        elif damage == 'overlap':
            struct.pack_into('<I', patched, central + 42, 0)
        else:
            struct.pack_into('<I', patched, central + 42, central - 10)
        with pytest.raises(AppError):
            packages.load(bytes(patched))


@pytest.mark.parametrize('value', ['', 'not base64', 'a' * (packages.PACKAGE_BASE64_LIMIT + 1), '====', None])
def test_base64_input_is_strict_and_bounded(value):
    with pytest.raises(AppError):
        packages.decode_input(value)


def test_raw_zip_and_base64_limits_are_separate_and_exact():
    assert packages.PACKAGE_LIMIT == 4_000_000
    assert packages.PACKAGE_BASE64_LIMIT == 5_333_336
    assert packages.decode_input(encoded(b'x' * 4_000_000)) == b'x' * 4_000_000
    # One extra raw byte still fits in the same encoded cap, but must be rejected.
    overflow = encoded(b'x' * 4_000_001)
    assert len(overflow) == packages.PACKAGE_BASE64_LIMIT
    with pytest.raises(AppError, match='decoded ZIP'):
        packages.decode_input(overflow)


def test_valid_package_between_previous_3mb_limit_and_4mb_raw_limit_roundtrips():
    manifest = deepcopy(MANIFEST)
    manifest['assets'].append({'path': 'assets/extra.png', 'mediaType': 'image/png'})
    files = contents('#amp-one{color:red}', manifest)
    files['assets/paper.png'] = PNG + os.urandom(1_600_000)
    files['assets/extra.png'] = PNG + os.urandom(1_600_000)
    raw = raw_zip(files)
    assert 3_000_000 < len(raw) <= packages.PACKAGE_LIMIT
    package = packages.load(packages.decode_input(encoded(raw)))
    canonical = packages.encode(package.files)
    assert 3_000_000 < len(canonical) <= packages.PACKAGE_LIMIT
    assert len(encoded(canonical)) <= packages.PACKAGE_BASE64_LIMIT
    assert packages.load(canonical).files['assets/extra.png'] == files['assets/extra.png']


def test_unused_large_assets_survive_but_referenced_assets_remain_runtime_bounded(monkeypatch):
    files = contents('#amp-one{color:red}')
    files['assets/paper.png'] = PNG + os.urandom(1_100_000)
    package = packages.load(raw_zip(files))
    assert len(package.css) < 1_000
    assert packages.load(packages.encode(package.files)).files['assets/paper.png'] == files['assets/paper.png']
    files['theme.css'] = b'#amp-one{background:url(assets/paper.png)}'
    def no_expansion(*args, **kwargs):
        raise AssertionError('Oversized runtime asset must be rejected before base64 expansion')
    monkeypatch.setattr(base64, 'b64encode', no_expansion)
    with pytest.raises(AppError, match='runtime limit'):
        packages.load(raw_zip(files))


def test_repeated_reference_amplification_and_license_metadata_are_bounded():
    files = contents('#amp-one{background:' + ','.join('url(assets/paper.png)' for _ in range(20)) + '}')
    files['assets/paper.png'] = PNG + os.urandom(100_000)
    with pytest.raises(AppError, match='runtime limit'):
        packages.load(raw_zip(files))
    files = contents('#amp-one{color:red}')
    files['licenses/OFL.txt'] = b'L' * (packages.CSS_LIMIT + 1)
    with pytest.raises(AppError, match='1 MB'):
        packages.load(raw_zip(files))


async def test_import_export_result_and_download_effect_never_apply(app):
    before, sid = deepcopy(app.state['theme']), app.state['selectedSessionId']
    saved = await imported(app)
    assert saved['format'] == 'zip' and saved['packageVersion'] == 1
    assert 'css' not in saved and 'base64' not in saved
    path = Path(saved['directory']) / saved['id'][6:]
    assert path.suffix == '.zip' and saved['fingerprint'] == hashlib.sha256(path.read_bytes()).hexdigest()
    row = await read(app, saved)
    assert row['fingerprint'] == saved['fingerprint']
    listing = (await dispatch(app, 'theme.list', {}))['result']
    listed = next(item for item in listing['items'] if item['id'] == saved['id'])
    assert listed['fingerprint'] == saved['fingerprint'] and 'css' not in listed and listed['assetCount'] == 2
    result = await exported(app, saved)
    value, effect = result['result'], result['effects'][0]
    assert value['format'] == 'zip' and value['mimeType'] == 'application/zip' and value['filename'] == 'paper.zip'
    assert effect['type'] == 'download' and effect['mimeType'] == effect['mime'] == 'application/zip'
    assert effect['content'] == {'base64': value['base64']}
    assert base64.b64decode(value['base64'], validate=True) == path.read_bytes()
    assert value['fingerprint'] == saved['fingerprint']
    assert app.state['theme'] == before and app.state['selectedSessionId'] == sid
    assert {item.name for item in path.parent.iterdir()} == {path.name}, 'No extracted assets or sidecars'


async def test_canonical_zip_import_is_idempotent_ignores_container_filename_and_metadata(app):
    first = await imported(app, fallbackName='download.unrelated')
    reordered = raw_zip(dict(reversed(list(contents().items()))), compression=zipfile.ZIP_DEFLATED, comment=b'ignored')
    repeated = await imported(app, packageBase64=encoded(reordered))
    assert repeated['id'] == first['id'] and repeated['name'] == 'Paper'
    exported_row = (await exported(app, first))['result']
    assert (await imported(app, packageBase64=exported_row['base64']))['id'] == first['id']
    renamed = await imported(app, name='My Paper', description='Personal')
    assert renamed['id'] != first['id'] and renamed['name'] == 'My Paper'
    assert renamed['description'] == 'Personal'


@pytest.mark.parametrize('args', [{}, {'css': '#amp-one{color:red}', 'packageBase64': 'AAAA'}])
async def test_import_exactly_one_input_schema_and_runtime_guard(app, args):
    with pytest.raises(AppError):
        await dispatch(app, 'theme.import', args)
    with pytest.raises(AppError, match='exactly one'):
        theme_library.import_appearance(app, args)
    assert not (app.data_dir / 'themes').exists()


async def test_bad_package_never_allocates_storage_or_changes_css(app):
    before = deepcopy(app.state['theme'])
    with pytest.raises(AppError):
        await imported(app, packageBase64=encoded(raw_zip({'notes.txt': b'not an appearance'})))
    assert not (app.data_dir / 'themes').exists() and app.state['theme'] == before


def notice_css(value):
    return theme_library.portable(theme_library.MARKER + json.dumps({
        packages.NOTICE_KEY: value,
    }) + ' */\n#amp-one{color:red}', 'Paper')


async def test_license_conflict_rejects_import_and_update_before_any_library_write(app):
    conflict = notice_css({'licenses/OFL.txt': 'A different license body'})
    with pytest.raises(AppError, match='conflicting license'):
        await imported(app, packageBase64=encoded(raw_zip(contents(conflict))))
    assert not (app.data_dir / 'themes').exists()
    saved = await imported(app)
    path = Path(saved['directory']) / saved['id'][6:]
    before = path.read_bytes()
    with pytest.raises(AppError, match='conflicting license'):
        await dispatch(app, 'theme.update', {
            'id': saved['id'], 'expectedFingerprint': saved['fingerprint'],
            'name': 'Conflicting', 'css': conflict,
        })
    assert path.read_bytes() == before and list(path.parent.iterdir()) == [path]
    assert (await read(app, saved))['fingerprint'] == saved['fingerprint']


async def test_identical_license_merge_and_nonconflicting_notices_survive_copies_and_exports(app):
    retained = {'licenses/OFL.txt': LICENSE, 'licenses/Additional.txt': 'Additional required attribution.\n'}
    source = notice_css(retained)
    saved = await imported(app, packageBase64=encoded(raw_zip(contents(source))))
    original = await read(app, saved)
    assert theme_library.metadata(original['css'])[0][packages.NOTICE_KEY] == retained
    updated = (await dispatch(app, 'theme.update', {
        'id': saved['id'], 'expectedFingerprint': saved['fingerprint'],
        'name': 'Updated Paper', 'css': source + '\n#amp-one{color:blue}',
    }))['result']
    zip_value = (await exported(app, updated))['result']
    assert theme_library.metadata(packages.load(base64.b64decode(zip_value['base64'])).css)[0][packages.NOTICE_KEY] == retained
    css_value = (await exported(app, updated, 'css'))['result']['css']
    copied = (await dispatch(app, 'theme.save', {'name': 'A copy', 'css': css_value}))['result']
    copied_zip = packages.load(base64.b64decode((await exported(app, copied))['result']['base64']))
    for path, body in retained.items():
        assert copied_zip.files[path].decode() == body
    assert theme_library.metadata(copied_zip.css)[0][packages.NOTICE_KEY] == retained


async def test_unchanged_zip_listings_compile_once_cache_only_metadata_and_full_read_recompiles(app, monkeypatch):
    saved = await imported(app)
    calls, original = [], packages.compile_css
    def counted(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)
    monkeypatch.setattr(packages, 'compile_css', counted)
    for _ in range(4):
        listed = theme_library.listing(app)
        row = next(item for item in listed['items'] if item['id'] == saved['id'])
        assert row['fingerprint'] == saved['fingerprint'] and 'css' not in row
    assert len(calls) == 1
    assert len(app._appearance_package_summaries) == 1
    allowed = {'name', 'description', 'source', 'tag', 'format', 'packageVersion', 'assetCount', 'licenseCount'}
    for cached in app._appearance_package_summaries.values():
        assert set(cached) == allowed
        assert len(json.dumps(cached)) < 2_000
        assert all(isinstance(value, (str, int)) for value in cached.values())
    full = theme_library.read(app, saved['id'])
    assert len(calls) == 2 and 'data:font/woff;base64,' in full['css']


async def test_zip_listing_digest_invalidates_unused_asset_and_never_reuses_invalid_bytes(app, monkeypatch):
    files = contents('#amp-one{color:red}')
    saved = await imported(app, packageBase64=encoded(raw_zip(files)))
    path = Path(saved['directory']) / saved['id'][6:]
    # STORED fixtures keep the same length. Restore mtime as well, demonstrating
    # why metadata-only timestamp/size invalidation is not sufficient.
    path.write_bytes(raw_zip(files))
    stamp = path.stat()
    calls, original = [], packages.load
    def counted(raw):
        calls.append(hashlib.sha256(raw).hexdigest())
        return original(raw)
    monkeypatch.setattr(packages, 'load', counted)
    first = next(row for row in theme_library.listing(app)['items'] if row['id'] == saved['id'])
    changed = bytearray(PNG)
    changed[-1] ^= 1
    files['assets/paper.png'] = bytes(changed)
    path.write_bytes(raw_zip(files))
    os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    assert path.stat().st_size == stamp.st_size and path.stat().st_mtime_ns == stamp.st_mtime_ns
    second = next(row for row in theme_library.listing(app)['items'] if row['id'] == saved['id'])
    assert second['fingerprint'] != first['fingerprint'] and len(calls) == 2
    assert theme_library.listing(app)['errors'] == [] and len(calls) == 2
    files['assets/paper.png'] = b'bad-signature' + PNG[len(b'bad-signature'):]
    path.write_bytes(raw_zip(files))
    os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    for _ in range(2):
        result = theme_library.listing(app)
        assert saved['id'] not in {row['id'] for row in result['items']}
        assert {row['name'] for row in result['errors']} == {path.name}
    assert len(calls) == 4
    assert hashlib.sha256(path.read_bytes()).hexdigest() not in app._appearance_package_summaries


async def test_zip_listing_summary_cache_is_bounded_and_eviction_revalidates(app, monkeypatch):
    saved = await imported(app)
    path = Path(saved['directory']) / saved['id'][6:]
    monkeypatch.setattr(theme_library, 'PACKAGE_SUMMARY_CACHE_LIMIT', 2)
    original_bytes = path.read_bytes()
    theme_library.listing(app)
    for name in ('Second', 'Third'):
        package = packages.with_metadata(packages.load(original_bytes), name=name)
        path.write_bytes(packages.encode(package.files))
        theme_library.listing(app)
    assert len(app._appearance_package_summaries) == 2
    digest = hashlib.sha256(original_bytes).hexdigest()
    assert digest not in app._appearance_package_summaries
    calls, original = [], packages.load
    def counted(raw):
        calls.append(True)
        return original(raw)
    monkeypatch.setattr(packages, 'load', counted)
    path.write_bytes(original_bytes)
    theme_library.listing(app)
    assert len(calls) == 1 and len(app._appearance_package_summaries) == 2
    assert digest in app._appearance_package_summaries


async def test_builtin_and_legacy_css_can_export_valid_zip_without_mutating_library(app):
    before = deepcopy(app.state['theme'])
    value = (await exported(app, {'id': 'builtin:default'}))['result']
    package = packages.load(base64.b64decode(value['base64']))
    assert package.manifest['type'] == 'appearance' and package.manifest['formatVersion'] == 1
    assert not (app.data_dir / 'themes').exists() and app.state['theme'] == before
    saved = (await dispatch(app, 'theme.import', {'css': '#amp-one{color:red}', 'fallbackName': 'Legacy'}))['result']
    assert saved['id'].endswith('.amplifier.css')
    zipped = (await exported(app, saved))['result']
    assert packages.load(base64.b64decode(zipped['base64'])).manifest['name'] == 'Legacy'
    legacy = (await exported(app, saved, 'css'))['effects'][0]
    assert legacy['mimeType'] == 'text/css' and isinstance(legacy['content'], str)
    assert app.state['theme'] == before

async def test_rename_and_css_update_preserve_original_assets_licenses_and_stale_fence(app):
    original = await imported(app)
    before = base64.b64decode((await exported(app, original))['result']['base64'])
    updated = (await dispatch(app, 'theme.update', {
        'id': original['id'], 'expectedFingerprint': original['fingerprint'],
        'name': 'Revised Paper', 'description': 'New colors',
        'css': '#amp-one{background:url("assets/paper.png");color:blue}',
    }))['result']
    assert updated['id'] == original['id'] and updated['fingerprint'] != original['fingerprint']
    for action in ('theme.update', 'theme.delete'):
        args = {'id': original['id'], 'expectedFingerprint': original['fingerprint']}
        if action == 'theme.update':
            args.update(name='Stale', css='#amp-one{color:red}')
        with pytest.raises(AppError) as error:
            await dispatch(app, action, args)
        assert error.value.status == 409
    value = (await exported(app, updated))['result']
    old, new = packages.load(before), packages.load(base64.b64decode(value['base64']))
    assert new.manifest['author'] == old.manifest['author']
    assert new.manifest['name'] == 'Revised Paper'
    for path in ('assets/paper.png', 'assets/body.woff', 'licenses/OFL.txt'):
        assert new.files[path] == old.files[path]
    assert theme_library.metadata(new.css)[0][packages.NOTICE_KEY]['licenses/OFL.txt'] == LICENSE
    # As with CSS, an in-place update keeps its historical ID. Importing this
    # changed canonical content uses its own content-derived import family.
    copied = await imported(app, packageBase64=value['base64'])
    assert (await imported(app, packageBase64=value['base64']))['id'] == copied['id']
    assert (await read(app, updated))['fingerprint'] == updated['fingerprint']


async def test_editor_can_update_compiled_css_without_losing_license_or_accumulating_headers(app):
    row = await imported(app)
    for index in range(3):
        value = await read(app, row)
        row = (await dispatch(app, 'theme.update', {
            'id': row['id'], 'expectedFingerprint': row['fingerprint'], 'name': f'Paper {index}',
            'css': value['css'] + '\n#amp-one{color:blue}',
        }))['result']
        current = await read(app, row)
        assert current['css'].count(theme_library.MARKER) == 1
        assert theme_library.metadata(current['css'])[0][packages.NOTICE_KEY] == {'licenses/OFL.txt': LICENSE}


async def test_css_export_save_copy_and_zip_from_css_retain_author_notices_and_embedded_assets(app):
    saved = await imported(app)
    css = (await exported(app, saved, 'css'))['result']['css']
    copy = (await dispatch(app, 'theme.save', {'name': 'Copy', 'css': css}))['result']
    assert copy['id'].endswith('.amplifier.css')
    copied = await read(app, copy)
    meta = theme_library.metadata(copied['css'])[0]
    assert meta['author'] == MANIFEST['author'] and meta[packages.NOTICE_KEY] == {'licenses/OFL.txt': LICENSE}
    bundle = packages.load(base64.b64decode((await exported(app, copy))['result']['base64']))
    assert bundle.files['licenses/OFL.txt'].decode() == LICENSE
    assert bundle.manifest['author'] == MANIFEST['author'] and bundle.manifest['assets'] == []
    assert 'data:image/png;base64,' in bundle.css and 'data:font/woff;base64,' in bundle.css


async def test_delete_applied_package_survives_restart_and_no_id_exports_only_committed_css(app, tmp_path):
    saved = await imported(app)
    value = await read(app, saved)
    await dispatch(app, 'theme.apply', {'name': value['name'], 'css': value['css']})
    applied = deepcopy(app.state['theme'])
    await dispatch(app, 'theme.preview', {'name': 'Unsaved', 'css': '#amp-one{color:green}'})
    assert (await exported(app, format='css'))['effects'][0]['content'] == applied['css']
    current_zip = packages.load(base64.b64decode((await exported(app))['result']['base64']))
    assert 'color:green' not in current_zip.css
    assert theme_library.metadata(current_zip.css)[0][packages.NOTICE_KEY] == {'licenses/OFL.txt': LICENSE}
    await dispatch(app, 'theme.delete', {'id': saved['id'], 'expectedFingerprint': saved['fingerprint']})
    assert app.state['theme'] == applied
    await app.close()
    restored = AppService(app.data_dir, workspace=tmp_path)
    try:
        assert restored.state['theme'] == applied
        assert theme_library.export(restored)['css'] == applied['css']
        raw = base64.b64decode(theme_library.export(restored, format='zip')['base64'])
        assert packages.load(raw).files['licenses/OFL.txt'].decode() == LICENSE
    finally:
        await restored.close()


@pytest.mark.parametrize('deleted_slot', [1, 2])
async def test_collision_gap_reuses_later_identical_import_without_overwriting(app, deleted_slot):
    first = await imported(app)
    original_zip = (await exported(app, first))['result']['base64']
    latest = first
    for index in range(deleted_slot):
        updated = (await dispatch(app, 'theme.update', {
            'id': latest['id'], 'expectedFingerprint': latest['fingerprint'], 'name': 'Paper',
            'css': f'#amp-one{{--collision:{index};color:blue}}',
        }))['result']
        latest = await imported(app, packageBase64=original_zip)
        assert latest['id'] != updated['id']
    await dispatch(app, 'theme.delete', {'id': updated['id'], 'expectedFingerprint': updated['fingerprint']})
    assert (await imported(app, packageBase64=original_zip))['id'] == latest['id']
    assert not (Path(first['directory']) / updated['id'][6:]).exists()


async def test_unused_asset_change_changes_stored_fingerprint_even_when_compiled_css_is_identical(app):
    saved = await imported(app, packageBase64=encoded(raw_zip(contents('#amp-one{color:red}'))))
    original = await read(app, saved)
    path = Path(saved['directory']) / saved['id'][6:]
    files = dict(packages.load(path.read_bytes()).files)
    files['assets/paper.png'] += b'Changed unused bytes'
    path.write_bytes(packages.encode(files))
    current = await read(app, saved)
    assert current['css'] == original['css'] and current['fingerprint'] != original['fingerprint']
    with pytest.raises(AppError) as error:
        await dispatch(app, 'theme.delete', {'id': saved['id'], 'expectedFingerprint': saved['fingerprint']})
    assert error.value.status == 409 and path.exists()


async def test_invalid_package_editor_update_does_not_replace_original_or_leave_temporary_files(app):
    saved = await imported(app)
    path = Path(saved['directory']) / saved['id'][6:]
    original = path.read_bytes()
    with pytest.raises(AppError):
        await dispatch(app, 'theme.update', {
            'id': saved['id'], 'expectedFingerprint': saved['fingerprint'], 'name': 'Bad',
            'css': '@import "https://example.invalid/bad.css";',
        })
    assert path.read_bytes() == original and list(path.parent.iterdir()) == [path]


async def test_zip_symlink_and_special_file_library_protection(app, tmp_path):
    saved = await imported(app)
    path = Path(saved['directory']) / saved['id'][6:]
    outside = tmp_path / 'outside.zip'
    outside.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(outside)
    for action, args in (
        ('theme.read', {'id': saved['id']}),
        ('theme.export', {'id': saved['id'], 'format': 'zip'}),
        ('theme.delete', {'id': saved['id'], 'expectedFingerprint': saved['fingerprint']}),
        ('theme.update', {'id': saved['id'], 'expectedFingerprint': saved['fingerprint'],
                          'name': 'No', 'css': '#amp-one{color:red}'}),
        ('theme.import', {'packageBase64': encoded()}),
    ):
        with pytest.raises(AppError):
            await dispatch(app, action, args)
    assert path.is_symlink() and packages.load(outside.read_bytes()).manifest['name'] == 'Paper'
    if hasattr(os, 'mkfifo'):
        os.mkfifo(path.parent / 'pipe.zip')
        errors = (await dispatch(app, 'theme.list', {}))['result']['errors']
        assert {row['name'] for row in errors} == {path.name, 'pipe.zip'}


async def test_zip_import_rejects_symlink_library_directory(app, tmp_path):
    outside = tmp_path / 'outside-library'
    outside.mkdir()
    (app.data_dir / 'themes').symlink_to(outside, target_is_directory=True)
    with pytest.raises(AppError, match='real directory'):
        await imported(app)
    assert not list(outside.iterdir())


async def test_concurrent_package_updates_have_one_winner(app):
    saved = await imported(app)
    def update(color):
        try:
            return theme_library.mutate(app, {
                'id': saved['id'], 'expectedFingerprint': saved['fingerprint'], 'name': 'Paper',
                'css': '#amp-one{color:' + color + '}',
            })
        except AppError as error:
            return error
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(update, ['blue', 'green']))
    assert sum(isinstance(row, AppError) for row in results) == 1
    assert next(row for row in results if isinstance(row, AppError)).status == 409


async def test_zip_collision_lookup_only_opens_existing_bounded_family_members(app, monkeypatch):
    saved = await imported(app)
    root, name = Path(saved['directory']), saved['id'][6:]
    content = (root / name).read_bytes()
    far_name = name[:-4] + '-1000.zip'
    (root / name).rename(root / far_name)
    (root / 'unrelated.zip').write_bytes(b'not an appearance')
    opened, original = [], theme_library.read_package
    def inspect(path, descriptor):
        opened.append(path.name)
        return original(path, descriptor)
    monkeypatch.setattr(theme_library, 'read_package', inspect)
    with theme_library.directory(app, writing=True) as descriptor:
        assert theme_library.import_name(descriptor, name, content) == far_name
    assert opened == [far_name]