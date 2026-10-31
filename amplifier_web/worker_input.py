"""Bounded async reads from the worker's synchronous Windows stdin pipe."""

import asyncio
import os
import queue
import threading


class ThreadedPipeReader:
    """One consumer, one requested line, no executor or read-ahead message queue.

    The daemon owns a private binary stream, never sys.stdin's buffered lock.
    Closing cancels delivery, not the OS read: a blocked read retains this one
    descriptor/thread until input/EOF or process exit. Never close that stream
    or join the thread from the loop; either can block shutdown indefinitely.
    This reader is scoped to a worker process, not a reconnectable transport.
    """

    def __init__(self, pipe, limit):
        self._loop = asyncio.get_running_loop()
        self._limit = limit
        self._requests = queue.Queue(maxsize=1)
        self._closed = threading.Event()
        self._pending = None
        self._eof = False
        # Binary mode also disables the Windows CRT's CRLF/Ctrl-Z translation.
        self._stream = os.fdopen(os.dup(pipe.fileno()), "rb")
        self._thread = threading.Thread(
            target=self._read, name="worker-stdin", daemon=True)
        try:
            self._thread.start()
        except BaseException:
            self._stream.close()
            raise

    async def readline(self):
        if self._closed.is_set():
            raise RuntimeError("Worker input is closed.")
        if self._eof:
            return b""
        if self._pending is not None:
            raise RuntimeError("Worker input already has a pending read.")
        future = self._pending = self._loop.create_future()
        self._requests.put_nowait(future)
        try:
            line = await future
            self._eof = not line
            return line
        except BaseException:
            self.close()
            raise
        finally:
            self._pending = None

    def close(self):
        self._closed.set()
        if self._pending is not None:
            self._pending.cancel()
        try:
            self._requests.put_nowait(None)
        except queue.Full:
            pass  # The queued read will observe _closed before touching stdin.

    def _deliver(self, future, line, error):
        if self._closed.is_set() or future.done():
            return
        if error is not None:
            future.set_exception(error)
        else:
            future.set_result(line)

    def _read(self):
        with self._stream:
            while True:
                future = self._requests.get()
                if self._closed.is_set() or future is None:
                    return
                line, error = None, None
                try:
                    line = self._stream.readline(self._limit + 1)
                    if len(line) > self._limit:
                        raise ValueError("Runtime message exceeds the transport limit.")
                except Exception as exc:
                    error = exc
                if self._closed.is_set():
                    return
                try:
                    self._loop.call_soon_threadsafe(self._deliver, future, line, error)
                except RuntimeError:
                    return  # The loop closed during cancellation.
                if error is not None or not line:
                    return