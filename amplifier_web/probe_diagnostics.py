"""Bounded provider subprocess evidence, summarized without copying SDK logs."""
import asyncio
import re


async def communicate(process, payload):
    """Read stdout while the separate bounded reader drains stderr."""
    async def write_input():
        try:
            process.stdin.write(payload)
            await process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            process.stdin.close()

    output, _, _ = await asyncio.gather(process.stdout.read(), write_input(), process.wait())
    return output


async def stderr_tail(stream):
    tail = b''
    while chunk := await stream.read(8192):
        tail = (tail + chunk)[-32768:]
    return tail.decode(errors='replace')


def failure_detail(stderr):
    # Never echo arbitrary stderr: it may contain credentials, URLs, paths or
    # request bodies. Emit only fixed descriptions and numeric version evidence.
    text = stderr.lower()
    rust = re.search(r'rustc (\d{1,4}\.\d{1,4}(?:\.\d{1,4})?) is not supported', text)
    required = re.search(r'requires rustc (\d{1,4}\.\d{1,4}(?:\.\d{1,4})?)', text)
    if rust:
        return ('Runtime build failed: Rust ' + rust[1] + ' is unsupported' +
                ('; a dependency requires Rust ' + required[1] if required else '') +
                '. Update the app to use the published runtime dependency.')
    for markers, detail in (
        (('no space left on device',), 'Runtime preparation failed: the server disk is full. Free space and retry.'),
        (('certificate_verify_failed', 'certificate verify failed'), 'The runtime download failed TLS certificate verification. Check the server trust configuration.'),
        (('could not resolve host', 'name or service not known', 'nodename nor servname'), 'The runtime download could not resolve its host. Check server DNS and connectivity.'),
        (('429 too many requests', 'rate limit exceeded'), 'The runtime download was rate limited. Wait for the remote limit to reset, then retry.'),
        (('no solution found', 'resolutionimpossible'), 'The runtime dependencies could not be resolved. Update the app and check its configured dependency sources.'),
        (('maturin.build_wheel', 'failed to build', 'failed building wheel'), 'A runtime dependency failed to build. Update the app to use published binaries, or check the configured source dependency toolchain.'),
        (('modulenotfounderror', 'importerror:'), 'The provider subprocess could not import a required module. Repair or update the runtime environment.'),
        (('permission denied', 'permissionerror:'), 'Runtime preparation was denied access to a required file. Check server file permissions.'),
    ):
        if any(marker in text for marker in markers):
            return detail
    return 'Check the runtime installation or update the app, then retry.'
