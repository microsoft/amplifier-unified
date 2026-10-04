"""Drive installed terminal clients without importing their runtime source.

The optional observer is a separately identified test dependency. All its process
records, client state and captures stay in the caller's owned fixture directory.
"""
import importlib.util
import json
import os
from pathlib import Path
import sys
import time
import traceback


directory = Path(sys.argv[1]).resolve()
spec = importlib.util.spec_from_file_location("terminal_probe", os.environ["COLD_PTY_OBSERVER"])
observer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(observer)
observer.ROOT = directory
probes, launches = {}, {}


def read_all():
    for probe in probes.values():
        probe.read(0)


def displayed(key, text):
    texts = text if isinstance(text, list) else [text]
    end = time.monotonic() + 25
    while time.monotonic() < end:
        read_all()
        if all(part in probes[key].text for part in texts):
            return probes[key].text
        if probes[key].process.poll() is not None:
            raise AssertionError(probes[key].text)
        time.sleep(.01)
    raise AssertionError(f"Missing {text!r}:\n{probes[key].text}")


def start(key):
    probes[key] = observer.Probe(launches[key], 120, 40, guard_terminal_modes=True,
        env={"PYTHONPATH": "", "HOME": str(directory / key / "home"),
             "XDG_CACHE_HOME": str(directory / key / "cache")}, cwd=str(directory))
    return displayed(key, "UNIFIED")


try:
    for line in sys.stdin:
        value = json.loads(line)
        key, op = value.get("key"), value["op"]
        try:
            if op == "start":
                launches[key] = value["args"]
                (directory / key / "home").mkdir(parents=True, exist_ok=True)
                result = start(key)
            elif op == "send":
                probes[key].send(value["text"].encode())
                result = True
            elif op == "wait":
                result = displayed(key, value["text"])
            elif op == "read":
                read_all()
                result = probes[key].text
            elif op == "restart":
                probes.pop(key).close()
                result = start(key)
            elif op == "save":
                read_all()
                result = {}
                for name, probe in probes.items():
                    (directory / (name + ".txt")).write_text(probe.text)
                    (directory / (name + ".ansi")).write_bytes(probe.raw)
                    result[name] = probe.text
            elif op == "close":
                for probe in probes.values():
                    probe.close()
                probes.clear()
                result = {"terminalModesRestored": True}
            else:
                raise ValueError(op)
            print(json.dumps({"id": value["id"], "result": result}), flush=True)
        except Exception as error:
            print(json.dumps({"id": value["id"], "error": str(error), "trace": traceback.format_exc()}), flush=True)
finally:
    for probe in probes.values():
        probe.close()
