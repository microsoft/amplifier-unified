"""Bounded, cancellable transport to the optional native observation library."""
import asyncio
import json
from pathlib import Path
import sys


def observation_metadata(data):
    """Only bounded public observation fields cross the helper boundary."""
    window = data.get("window")
    if not isinstance(window, dict):
        raise ValueError("Missing native window identity")
    result = {}
    for key, limit in (("id", 120), ("title", 300), ("application", 200)):
        value = window.get(key)
        if value is None and key == "application":
            result[key] = None
        elif not isinstance(value, str) or len(value) > limit:
            raise ValueError("Invalid native window identity")
        else:
            result[key] = value
    bounds = window.get("bounds")
    if (not isinstance(bounds, list) or len(bounds) != 4 or any(type(n) is not int for n in bounds)
            or not 0 < bounds[2]-bounds[0] <= 16000 or not 0 < bounds[3]-bounds[1] <= 16000):
        raise ValueError("Invalid native window bounds")
    result["bounds"] = bounds
    return {"window": result, "captureScope": "visible foreground window region", "reportedBy": "native-host"}


class NativeForeground:
    async def run(self, operation):
        if operation not in {"status", "capture"}:
            raise ValueError("Unsupported native observation operation")
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-I", str(Path(__file__).with_name("native_foreground_helper.py")), operation,
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, limit=800001,
        )
        try:
            async with asyncio.timeout(3 if operation == "status" else 6):
                chunks, size = [], 0
                while chunk := await process.stdout.read(min(65536, 800001-size)):
                    size += len(chunk)
                    if size > 800000:
                        raise ValueError("Native observation exceeded output limit")
                    chunks.append(chunk)
                data = b"".join(chunks)
                await process.wait()
                if process.returncode:
                    raise ValueError("Native observation helper failed")
                result = json.loads(data)
                if not isinstance(result, dict):
                    raise ValueError("Invalid native observation response")
                return result
        finally:
            if process.returncode is None:
                process.kill()
            await process.wait()
