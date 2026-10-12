import asyncio
import json
import os
import shutil
import httpx

async def public_repository_read(endpoint):
    """Anonymous metadata lookup; fixed GitHub origin and bounded response."""
    async with httpx.AsyncClient(timeout=45, follow_redirects=False) as client:
        async with client.stream("GET", "https://api.github.com/" + endpoint.lstrip("/"), headers={"Accept":"application/vnd.github+json"}) as response:
            if response.status_code != 200:
                return None
            output = bytearray()
            async for chunk in response.aiter_bytes():
                output.extend(chunk)
                if len(output) > 4_000_000:
                    raise ValueError("GitHub response exceeded the bounded feedback read")
            result = json.loads(output)
            if not isinstance(result, (dict, list)):
                raise ValueError("GitHub returned an invalid receipt")
            return result

async def github_api(endpoint, payload, *, method=None):
    """Structured stdin keeps user text out of shell evaluation and process args."""
    read = payload is None and (method is None or method == 'GET') and endpoint.lstrip('/').startswith('repos/')
    if read:
        try:
            result = await public_repository_read(endpoint)
        except httpx.HTTPError:
            result = None
        if result is not None:
            return result
        if os.environ.get('AMPLIFIER_GITHUB_ACCESS') == 'anonymous':
            raise RuntimeError('The repository could not be read anonymously. Private repositories require authorized access.')
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
