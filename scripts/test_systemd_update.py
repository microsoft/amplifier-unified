#!/usr/bin/env python3
"""Exercise update handoff against real systemd in a disposable Docker container.

Requires Docker with Linux containers and privileged-container support. No host
ports, service units, credentials, or writable repository mounts are passed in.
Package installation is local/synthetic; systemd, cgroups, signals, HTTP health,
update activation, persisted receipts, and readiness reconciliation are real.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import os
import subprocess
import sys
import time
import uuid

IMAGE = "amplifier-update-systemd-test"
DOCKERFILE = """FROM python:3.13-slim
RUN apt-get update && apt-get install -y --no-install-recommends systemd systemd-sysv dbus-user-session git ca-certificates libpam0g && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir uv
ENV container=docker
CMD [\"/sbin/init\"]
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docker", default=shutil.which("docker"), help="Docker executable")
    args = parser.parse_args()
    if not args.docker:
        parser.error("Docker is required; this test does not silently substitute mocks")
    root = Path(__file__).resolve().parents[1]
    name = "amplifier-update-test-" + uuid.uuid4().hex[:12]

    def docker(*command, **kwargs):
        return subprocess.run([args.docker, *command], check=True, **kwargs)

    docker("build", "-t", IMAGE, "-", input=DOCKERFILE, text=True)
    docker("run", "-d", "--name", name, "--privileged", "--cgroupns=private",
           "--tmpfs", "/run", "--tmpfs", "/run/lock", "--tmpfs", "/tmp",
           "--mount", f"type=bind,source={root},target=/src,readonly", IMAGE)
    try:
        for _ in range(60):
            status = docker("exec", name, "systemctl", "show", "--property=SystemState", "--value",
                            capture_output=True, text=True).stdout.strip()
            if status in {"running", "degraded"}:
                break
            time.sleep(.5)
        else:
            raise RuntimeError("Disposable systemd manager did not boot")
        docker("exec", name, "useradd", "-m", "-u", "1234", "updater")
        docker("exec", name, "loginctl", "enable-linger", "updater")
        docker("exec", name, "systemctl", "start", "user@1234.service")
        docker("exec", name, "uv", "venv", "/opt/test")
        docker("exec", name, "uv", "pip", "install", "--python", "/opt/test/bin/python", "/src")
        historical = subprocess.run(['git', '-C', str(root), 'show',
            'fd12fb041dbf71050257e8f97b2605b33cec0492:amplifier_web/app_updates.py'],
            check=True, capture_output=True, env={**os.environ, 'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_CONFIG_NOSYSTEM': '1'}).stdout
        docker("exec", "-i", name, "/opt/test/bin/python", "-c",
               "import sys; from pathlib import Path; Path('/opt/legacy-app-updates.py').write_bytes(sys.stdin.buffer.read())",
               input=historical)
        docker("exec", name, "/opt/test/bin/python", "/src/tests/fixtures/systemd_update_service.py", "prepare")
        docker("exec", "--user", "1234", "--env", "HOME=/home/updater",
               "--env", "XDG_RUNTIME_DIR=/run/user/1234",
               "--env", "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1234/bus", name,
               "/opt/test/bin/python", "/src/tests/fixtures/systemd_update_service.py", "exercise")
    except BaseException:
        subprocess.run([args.docker, "exec", name, "journalctl", "--no-pager", "-n", "120",
                        "_SYSTEMD_USER_UNIT=amplifier-unified.service", "+", "_SYSTEMD_UNIT=user@1234.service"], check=False)
        raise
    finally:
        subprocess.run([args.docker, "rm", "-f", name], check=False, stdout=subprocess.DEVNULL)


if __name__ == "__main__":
    main()
