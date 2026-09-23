"""File notifications invalidate history summaries; reconciliation remains authoritative."""
from pathlib import Path
import threading


class HistoryWatch:
    def __init__(self, root):
        self.root = Path(root)
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.ready = False
        self.dirty = set()
        self.thread = threading.Thread(target=self._run, name='native-history-watch', daemon=True)
        self.thread.start()

    def _project(self, path):
        try:
            parts = Path(path).relative_to(self.root).parts
        except ValueError:
            return None
        if not parts:
            return '*'
        # Directory creation/deletion and the files actually used by the
        # catalog. Runtime events and browser view checkpoints are irrelevant.
        if len(parts) <= 3 or (len(parts) == 4 and parts[3] in {
            'metadata.json', 'metadata.json.backup', 'transcript.jsonl',
            'transcript.jsonl.backup', 'naming.json', 'context-intelligence',
        }) or (len(parts) == 5 and parts[3:] == ('context-intelligence', 'metadata.json')):
            return parts[0]
        return None

    def _run(self):
        try:
            from watchfiles import watch
            for changes in watch(self.root, watch_filter=None, stop_event=self.stop,
                                 debounce=200, step=50, rust_timeout=500,
                                 yield_on_timeout=True, poll_delay_ms=15000,
                                 ignore_permission_denied=False):
                with self.lock:
                    if not self.ready:
                        # Cover the interval before the OS watches were ready.
                        self.dirty.add('*')
                    self.ready = True
                    self.dirty.update(project for _, path in changes
                                      if (project := self._project(path)) is not None)
        except Exception:
            # Missing roots, unsupported watches, permissions or exhausted OS
            # resources must fall back to the ordinary stat reconciliation.
            pass
        finally:
            with self.lock:
                self.ready = False

    def take(self):
        with self.lock:
            result = self.ready, self.dirty
            self.dirty = set()
            return result

    def unchanged(self):
        with self.lock:
            return self.ready and not self.dirty

    def close(self):
        self.stop.set()
        self.thread.join(timeout=2)
