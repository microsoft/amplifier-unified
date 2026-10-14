import asyncio
import json
import os
import shutil
async def github_api(endpoint, payload, *, method=None):
    """Structured stdin keeps user text out of shell evaluation and process args."""
    executable = shutil.which("gh")
    if not executable:
        raise FileNotFoundError("GitHub CLI is not installed")
    child = await asyncio.create_subprocess_exec(
        executable, "api", "--hostname", "github.com", "--method", method or ("POST" if payload is not None else "GET"),
        endpoint, *(["--input", "-"] if payload is not None else []),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        env={**os.environ, "GH_PROMPT_DISABLED": "1", "GH_PAGER": "cat"},
    )
    async def exchange():
        async def send():
            if payload is not None:
                child.stdin.write(json.dumps(payload).encode())
                await child.stdin.drain()
            child.stdin.close()
        async def receive():
            result = bytearray()
            while chunk := await child.stdout.read(65536):
                result.extend(chunk)
                if len(result) > 4_000_000:
                    raise ValueError('GitHub response exceeded the bounded feedback read')
            return bytes(result)
        _, output = await asyncio.gather(send(), receive())
        await child.wait()
        return output
    try:
        output = await asyncio.wait_for(exchange(), 45)
    except BaseException:
        if child.returncode is None:
            child.kill()
        await child.wait()
        raise
    if child.returncode:
        # Do not publish CLI stderr, credentials, paths, or a false failure claim
        # after a request may already have reached GitHub.
        raise RuntimeError("GitHub did not acknowledge the request")
    result = json.loads(output)
    if not isinstance(result, (dict, list)):
        raise ValueError("GitHub returned an invalid receipt")
    return result
