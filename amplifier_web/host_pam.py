"""Optional single-owner login through Constellation's private host PAM socket."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import stat

TIMEOUT_SECONDS = 20


def validate(value):
    if value is None:
        return None
    if (os.name == "nt" or not isinstance(value, dict)
            or set(value) != {"owner", "socket"}):
        raise ValueError("host_pam requires owner and socket on a Unix host, or null.")
    owner, path = value["owner"], value["socket"]
    if (not isinstance(owner, str) or not owner or owner != owner.strip()
            or len(owner) > 256 or any(ord(char) < 32 for char in owner)):
        raise ValueError("host_pam.owner must name one host account.")
    if (not isinstance(path, str) or not path or "\0" in path
            or not Path(path).is_absolute()):
        raise ValueError("host_pam.socket must be an absolute Unix socket path.")
    return {"owner": owner, "socket": path}


async def authenticate(config: dict, username: str, password: str) -> bool:
    """Use the existing owner-login.sock protocol; never retain credentials.

    The installing owner supplies this private local socket. The host bridge
    applies its fixed-owner PAM policy, account checks and attempt limits.
    Unavailable or invalid bridges fail closed, without a local PAM fallback.
    """
    if username != config["owner"] or not password or len(password) > 1024:
        return False
    writer = None
    try:
        info = Path(config["socket"]).lstat()
        if (not stat.S_ISSOCK(info.st_mode) or info.st_mode & 0o077
                or info.st_uid not in {0, os.geteuid()}):
            return False
        async with asyncio.timeout(TIMEOUT_SECONDS):
            reader, writer = await asyncio.open_unix_connection(config["socket"], limit=64)
            writer.write((json.dumps({"username": username, "password": password},
                                     ensure_ascii=False) + "\n").encode("utf-8"))
            await writer.drain()
            return await reader.readline() == b"OK\n" and await reader.read(1) == b""
    except (OSError, ValueError, TimeoutError):
        return False
    finally:
        if writer is not None:
            writer.close()
