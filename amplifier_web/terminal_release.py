"""Resolve current, qualified terminal artifacts without freezing future installs."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from packaging.version import InvalidVersion, Version

from .deployment import write_private

REPOSITORY = 'microsoft/amplifier-app-tui'
PROTOCOL_VERSION = 1
MAX_WHEEL_BYTES = 16_000_000
MAX_RECEIPT_BYTES = 256_000


@dataclass(frozen=True)
class TerminalRelease:
    version: str
    filename: str
    sha256: str
    wheel: bytes


async def github(endpoint: str, *, maximum: int, binary: bool = False) -> bytes:
    """Bound authenticated downloads; never expose gh credentials or response bodies."""
    if not shutil.which('gh'):
        raise ValueError('The server needs GitHub release access to prepare terminal setup.')
    process = await asyncio.create_subprocess_exec(
        'gh', 'api', '--hostname', 'github.com', endpoint, '-H',
        'Accept: application/octet-stream' if binary else 'Accept: application/vnd.github+json',
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    try:
        async with asyncio.timeout(90):
            result = bytearray()
            while chunk := await process.stdout.read(min(65536, maximum + 1 - len(result))):
                result.extend(chunk)
                if len(result) > maximum:
                    raise ValueError('The terminal release exceeds the supported download size.')
            if await process.wait():
                raise ValueError('The server could not download the terminal release. Check its GitHub access and retry.')
            return bytes(result)
    except TimeoutError:
        raise ValueError('The terminal release lookup timed out. Please retry.') from None
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


def release_version(tag):
    # Tags enter filenames and a shell template. Reject arbitrary tag spelling,
    # local builds and aliases even when packaging can normalize them.
    if not isinstance(tag, str) or not re.fullmatch(r'v[0-9][0-9a-z.]{0,79}', tag):
        return None
    try:
        version = Version(tag[1:])
    except InvalidVersion:
        return None
    return version if str(version) == tag[1:] else None


def asset_spec(asset, maximum):
    if not isinstance(asset, dict):
        raise ValueError('The terminal release is missing a verified artifact.')  # noqa: TRY004 - untrusted release data
    identity, size, digest = asset.get('id'), asset.get('size'), asset.get('digest')
    if (type(identity) is not int or identity <= 0 or type(size) is not int
            or not 0 < size <= maximum or asset.get('state') != 'uploaded'
            or not isinstance(digest, str) or not re.fullmatch(r'sha256:[0-9a-f]{64}', digest)):
        raise ValueError('The terminal release has incomplete artifact verification data.')
    return identity, size, digest[7:]


async def artifact(asset, maximum, cache):
    identity, size, digest = asset_spec(asset, maximum)
    path = Path(cache) / digest
    if path.is_file() and path.stat().st_size == size:
        value = path.read_bytes()
        if hashlib.sha256(value).hexdigest() == digest:
            return value
    value = await github(f'repos/{REPOSITORY}/releases/assets/{identity}', maximum=maximum, binary=True)
    if len(value) != size or hashlib.sha256(value).hexdigest() != digest:
        raise ValueError('The terminal download failed verification. No installer was created.')
    write_private(path, value)
    return value


async def latest_release(spec, cache) -> TerminalRelease:
    """Resolve afresh for a new setup; retrying a prepared setup bypasses this."""
    candidates = []
    # GitHub's /latest omits prereleases. Paginate to avoid freezing on an old
    # final release or silently selecting an older item from a truncated page.
    for page in range(1, 11):
        raw = await github(f'repos/{REPOSITORY}/releases?per_page=100&page={page}', maximum=4_000_000)
        try:
            rows = json.loads(raw)
        except (ValueError, UnicodeError):
            raise ValueError('The terminal release catalog could not be read.') from None
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError('The terminal release catalog could not be read.')
        for row in rows:
            version = release_version(row.get('tag_name'))
            if version is not None and row.get('draft') is False and row.get('published_at'):
                candidates.append((version, row))
        if len(rows) < 100:
            break
    else:
        raise ValueError('The terminal release catalog exceeds the supported lookup size.')

    for version, row in sorted(candidates, key=lambda item: item[0], reverse=True):
        filename = f'amplifier_app_tui-{version}-py3-none-{spec["tag"]}.whl'
        assets = row.get('assets')
        if not isinstance(assets, list) or any(not isinstance(item, dict) for item in assets):
            raise ValueError('The terminal release artifact catalog could not be read.')
        matching = [item for item in assets if item.get('name') in {filename, filename + '.receipt.json'}]
        if not matching:
            continue  # No build for this supported platform.
        by_name = {item['name']: item for item in matching}
        if len(matching) != 2 or len(by_name) != 2:
            raise ValueError('The newest terminal build is missing its qualification receipt. Please retry after publication completes.')
        receipt_bytes = await artifact(by_name[filename + '.receipt.json'], MAX_RECEIPT_BYTES, cache)
        try:
            receipt = json.loads(receipt_bytes)
        except (ValueError, UnicodeError):
            raise ValueError('The terminal qualification receipt could not be read.') from None
        if not isinstance(receipt, dict):
            raise ValueError('The terminal qualification receipt could not be read.')  # noqa: TRY004 - untrusted release data
        protocol = receipt.get('connected_protocol_version')
        if type(protocol) is not int or protocol != PROTOCOL_VERSION:
            continue  # This host only installs explicitly qualified protocol v1 clients.
        _, _, digest = asset_spec(by_name[filename], MAX_WHEEL_BYTES)
        required = ('tracked_source_clean', 'doctor_passed', 'state_untouched',
                    'connected_install_without_execution_dependencies', 'installed_native_bytes_match',
                    'installed_native_load_passed', 'artifact_privacy_scan_passed')
        if (receipt.get('version') != str(version) or receipt.get('wheel') != filename
                or receipt.get('wheel_sha256') != digest or receipt.get('platform') != spec['system']
                or receipt.get('architecture') not in spec['architectures']
                or not isinstance(receipt.get('source_commit'), str)
                or not re.fullmatch('[0-9a-f]{40}', receipt['source_commit'])
                or any(receipt.get(key) is not True for key in required)):
            raise ValueError('The terminal release did not pass qualification. No installer was created.')
        wheel = await artifact(by_name[filename], MAX_WHEEL_BYTES, cache)
        return TerminalRelease(str(version), filename, digest, wheel)
    raise ValueError('No qualified terminal release supports this platform and service protocol yet. Ask the service owner to publish one.')
