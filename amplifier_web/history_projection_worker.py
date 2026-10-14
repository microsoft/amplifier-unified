"""Disposable selected-history computation. No command or application-state owner.

Only bounded descriptors cross the pipes. The host owns the private spool;
canonical paths and generations remain inputs of the read-model owner.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import shutil
import sys
import threading

from .history_projection_failure import STAGES, capture_failure

INPUT_LIMIT = 32 * 1024 * 1024
OUTPUT_LIMIT = 64 * 1024 * 1024
MEMORY_LIMIT = 768 * 1024 * 1024
DESCRIPTOR_LIMIT = 4096
RECEIPT_LIMIT = 256 * 1024
RECEIPT_PATHS = 2048
IDENTITY = re.compile(r'[0-9a-f]{32}\Z')


class MemoryLimit(Exception):
    pass


def memory_policy(limit):
    """Hard address-space/commit bound plus a cooperative resident check.

    Unix address space is not an RSS guarantee. Windows uses a process-memory
    Job Object; failure to establish that bound refuses the worker.
    """
    if sys.platform == 'win32':
        import ctypes as c
        from ctypes import wintypes as w
        size = c.c_size_t
        class Basic(c.Structure):
            _fields_ = [('process_time', c.c_longlong), ('job_time', c.c_longlong),
                ('flags', w.DWORD), ('minimum', size), ('maximum', size),
                ('active', w.DWORD), ('affinity', size), ('priority', w.DWORD),
                ('scheduling', w.DWORD)]
        class Limits(c.Structure):
            _fields_ = [('basic', Basic), ('io', c.c_ulonglong * 6),
                ('process_memory', size), ('job_memory', size),
                ('peak_process', size), ('peak_job', size)]
        kernel = c.WinDLL('kernel32', use_last_error=True)
        kernel.CreateJobObjectW.restype = w.HANDLE
        kernel.GetCurrentProcess.restype = w.HANDLE
        kernel.SetInformationJobObject.argtypes = [w.HANDLE, c.c_int, c.c_void_p, w.DWORD]
        kernel.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
        handle = kernel.CreateJobObjectW(None, None)
        limits = Limits(); limits.basic.flags = 0x100 | 0x2000
        limits.process_memory = limit
        if (not handle or not kernel.SetInformationJobObject(handle, 9, c.byref(limits), c.sizeof(limits))
                or not kernel.AssignProcessToJobObject(handle, kernel.GetCurrentProcess())):
            raise MemoryLimit()
        # Keep the handle alive until process exit: closing it retires this job.
        def check():
            return handle
        return check
    import resource
    soft, hard = resource.getrlimit(resource.RLIMIT_AS)
    cap = min(limit, hard) if hard != resource.RLIM_INFINITY else limit
    resource.setrlimit(resource.RLIMIT_AS, (cap, hard))
    def check():
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        if peak * (1 if sys.platform == 'darwin' else 1024) > limit:
            raise MemoryLimit()
    check()
    return check


def write_json(path, value, limit):
    digest, count = hashlib.sha256(), 0
    temporary = path.with_suffix('.writing')
    try:
        with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as stream:
            for chunk in json.JSONEncoder(ensure_ascii=False, allow_nan=False,
                                          separators=(',', ':')).iterencode(value):
                data = chunk.encode('utf-8'); count += len(data)
                if count > limit:
                    raise OverflowError()
                stream.write(data); digest.update(data)
        os.replace(temporary, path)
        return count, digest.hexdigest()
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def source_receipt(value):
    """Small display-generation receipt only; never retain the rejected tree."""
    revisions = value.get('revisions')
    paths = value.get('paths')
    if paths is None and isinstance(revisions, (list, tuple)) and len(revisions) <= RECEIPT_PATHS:
        paths = [row[0] if isinstance(row, (list, tuple)) and len(row) == 2 else None for row in revisions]
    if (not isinstance(paths, (list, tuple)) or not isinstance(revisions, (list, tuple))
            or len(paths) != len(revisions) or len(paths) > RECEIPT_PATHS):
        return None
    for path, revision in zip(paths, revisions):
        if (not isinstance(path, str) or not path or len(path) > 4096
                or not isinstance(revision, (list, tuple)) or len(revision) != 2
                or revision[0] != path):
            return None
        stamp = revision[1]
        if stamp is not None and (not isinstance(stamp, (list, tuple)) or len(stamp) != 4
                or not isinstance(stamp[0], (list, tuple)) or len(stamp[0]) != 2
                or any(type(item) is not int for item in (*stamp[0], *stamp[1:]))):
            return None
    receipt = {'status': 'retry', 'reason': 'output_limit', 'revisions': revisions}
    if len(json.dumps(receipt, ensure_ascii=False, allow_nan=False,
                      separators=(',', ':')).encode('utf-8')) > RECEIPT_LIMIT:
        return None
    return receipt


def run(spool, cache_home, *, model_factory=None, memory_limit=MEMORY_LIMIT):
    spool, cache_home = Path(spool).resolve(), Path(cache_home).resolve()
    work = queue.Queue(maxsize=1)
    stopped = threading.Event()
    output_lock = threading.Lock()
    def reply(value):
        line = json.dumps(value, separators=(',', ':')).encode() + b'\n'
        if len(line) > DESCRIPTOR_LIMIT:
            raise ValueError('Oversized descriptor')
        with output_lock:
            sys.stdout.buffer.write(line); sys.stdout.buffer.flush()

    def compute():
        stage = 'setup_policy'
        def failed(reason, identity=None, exception=None):
            value = {'type': 'retry', 'reason': reason, 'failure': capture_failure(stage, exception)}
            if identity is not None:
                value['id'] = identity
            reply(value)
        def set_stage(value):
            nonlocal stage
            if isinstance(value, str) and value in STAGES:
                stage = value
        try:
            guard = memory_policy(memory_limit)
            stage = 'setup_import'
            factory = model_factory
            if factory is None:
                from .history_read_model import SelectedHistoryReadModel
                factory = SelectedHistoryReadModel
            stage = 'setup_store'
            model = factory(cache_home)
            reply({'type': 'ready'})
        except (MemoryError, MemoryLimit) as exc:
            failed('memory_limit', exception=exc); return
        except Exception as exc:
            failed('worker_failed', exception=exc); return
        while not stopped.is_set():
            descriptor = work.get()
            if descriptor is None:
                return
            identity = descriptor['id']
            source, target = spool / (identity + '.input'), spool / (identity + '.output')
            def check_budget():
                if stopped.is_set():
                    raise InterruptedError()
                guard()
            check_budget.stage = set_stage
            try:
                stage = 'input_decode'
                check_budget()
                with source.open('rb') as stream:
                    data = stream.read(INPUT_LIMIT + 1)
                if len(data) > INPUT_LIMIT:
                    raise OverflowError('input')
                if hashlib.sha256(data).hexdigest() != descriptor['digest']:
                    raise ValueError('Changed input artifact')
                inputs = json.loads(data); del data
                if not isinstance(inputs, dict):
                    raise ValueError('Expected mapping')
                stage = 'model'
                result = model.project(inputs, check_budget=check_budget)
                del inputs
                check_budget()
                stage = 'output_encode'
                try:
                    size, digest = write_json(target, result, OUTPUT_LIMIT)
                except OverflowError:
                    receipt = source_receipt(result) if result.get('status') == 'complete' else None
                    if receipt is None:
                        raise
                    size, digest = write_json(target, receipt, min(OUTPUT_LIMIT, RECEIPT_LIMIT))
                del result
                check_budget()
                reply({'type': 'result', 'id': identity, 'bytes': size, 'digest': digest})
            except (MemoryError, MemoryLimit) as exc:
                target.unlink(missing_ok=True)
                failed('memory_limit', identity, exc)
                return
            except OverflowError as exc:
                reply({'type': 'retry', 'id': identity,
                       'reason': 'input_limit' if str(exc) == 'input' else 'output_limit'})
            except InterruptedError:
                return
            except Exception as exc:
                target.unlink(missing_ok=True)
                failed('worker_failed', identity, exc)

    thread = threading.Thread(target=compute, daemon=True)
    thread.start()
    abandoned = False
    try:
        while True:
            line = sys.stdin.buffer.readline(DESCRIPTOR_LIMIT + 1)
            if not line:
                abandoned = True
                break
            if len(line) > DESCRIPTOR_LIMIT or not line.endswith(b'\n'):
                break
            value = json.loads(line)
            if value == {'stop': True}:
                break
            if (set(value) != {'id', 'digest'} or not IDENTITY.fullmatch(value['id'])
                    or not re.fullmatch(r'[0-9a-f]{64}', value['digest'])):
                break
            work.put_nowait(value)
    except (ValueError, TypeError, KeyError, queue.Full):
        pass
    finally:
        stopped.set()
        try: work.put_nowait(None)
        except queue.Full: pass
        thread.join(timeout=1)
        shutil.rmtree(spool, ignore_errors=True)
        # Explicit stop preserves pending host inputs for a replacement worker.
        # Bare EOF means the owning host disappeared; reclaim its private spool
        # too, including queued files the worker never received.
        parent = spool.parent
        if abandoned and parent.parent == cache_home and parent.name.startswith('projection-spool-'):
            shutil.rmtree(parent, ignore_errors=True)
        # EOF must retire even a blocked/misbehaving projection thread.
        os._exit(0)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--spool', required=True)
    parser.add_argument('--cache-home', required=True)
    parser.add_argument('--memory-limit', type=int, default=MEMORY_LIMIT)
    args = parser.parse_args()
    run(args.spool, args.cache_home, memory_limit=args.memory_limit)
