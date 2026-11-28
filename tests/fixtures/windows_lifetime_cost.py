"""Native cost measurement. Called inside the guarded standard-user journey."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import statistics
import subprocess
import sys
import time


def measure(home, installed):
    from amplifier_web import windows_service_lifetime as lifetime
    from amplifier_web import windows_task_runtime as runtime
    from amplifier_web.windows_files import directory
    home = Path(home)
    with directory(home, owned_root=home, create=True):
        pass
    spec = replace(installed, data_dir=str(home), workspace=str(home),
                   repeat_start="2026-11-28T01:02:03+00:00")

    def snapshot():
        return {str(p.relative_to(home)): (p.stat().st_size, p.stat().st_mtime_ns,
                hashlib.sha256(p.read_bytes()).hexdigest())
                for p in home.rglob("*") if p.is_file()}

    # Exact runtime records already read by Runner's old and new Stop check.
    record = {"schema": 1, "registration": spec.registration_id, "run": "b" * 32,
              "pid": 1, "created": "134000000000000001", "image": installed.python,
              "phase": "listening"}
    runtime.publish(spec, record)
    intent = lifetime.owner_choice(spec, stop=False)
    runner = runtime.Runner.__new__(runtime.Runner)
    runner.spec, runner.record = spec, record
    host = {}
    for name, check, extra_reads in (
        ("before", lambda: lifetime.allowed(spec, intent) and not runner.stop_requested(), 1),
        ("after", lambda: not runner.stop_requested(), 0),
    ):
        wall, cpu = time.perf_counter(), time.process_time()
        for _ in range(100):
            assert check()
        host[name] = {"wallMsPerCheck": (time.perf_counter() - wall) * 10,
                      "cpuMsPerCheck": (time.process_time() - cpu) * 10,
                      "extraLifetimeReadsPerCheck": extra_reads}

    # This invokes the real cold launcher entry with a fixed synthetic query
    # receipt. No new main-task XML is installed. Scheduler COM time is excluded.
    source = """
import json,sys,time
cpu=time.process_time()
from amplifier_web import windows_service as service
from amplifier_web import windows_service_lifetime as lifetime
spec=service.TaskSpec.decode(sys.argv[1])
service._read=lambda home: ({'state':4,'instances':1},spec)
try:
 service._run(spec.encoded())
except SystemExit as e:
 assert e.code == 0
else:
 raise AssertionError('paused launcher did not end')
print(json.dumps({'cpuMs':(time.process_time()-cpu)*1000}))
"""
    ticks = {}
    for name in ("stopped", "budget"):
        lifetime.owner_choice(spec, stop=name == "stopped", manual=False)
        if name == "budget":
            for _ in range(3):
                lifetime.admit(spec)
        before = snapshot()
        walls, cpus = [], []
        for _ in range(5):
            started = time.perf_counter()
            completed = subprocess.run([sys.executable, "-I", "-B", "-c", source, spec.encoded()],
                                       capture_output=True, text=True, timeout=30)
            assert completed.returncode == 0, completed.stderr[-1000:]
            walls.append((time.perf_counter() - started) * 1000)
            cpus.append(json.loads(completed.stdout)["cpuMs"])
        after = snapshot()
        changed = sorted(k for k in before.keys() | after.keys() if before.get(k) != after.get(k))
        assert not changed, changed
        ticks[name] = {"samples": 5, "wallMedianMs": statistics.median(walls),
                       "wallMaxMs": max(walls), "cpuMedianMs": statistics.median(cpus),
                       "changedFiles": changed,
                       "scope": "cold real _run; synthetic Scheduler query; COM time excluded"}
    return {"hostChecks": host, "ticks": ticks}