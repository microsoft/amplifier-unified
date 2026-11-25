"""Pytest-only checkout cleanup inputs, NOT official serving-wheel eligibility.

Reviewed first-party tests may exercise unpublished code on their owned disposable
runner. Production Gate, effect journal, loaded-input transport and the original
permit callback are unchanged. No runtime selector or qualification is installed.
"""
import hashlib
import json
from pathlib import Path
import uuid

from amplifier_web import environment_cleanup_execution as execution


class CheckoutGate(execution.Gate):
    def __init__(self, app):
        self.paths = {'amplifier_web/' + name: Path(execution.__file__).with_name(name)
                      for name in (*execution.NAMES, execution.LAUNCHER)}
        self.raw = {name: path.read_bytes() for name, path in self.paths.items()}
        self.members = [{'name': 'amplifier_web/' + name,
                         'bytes': len(self.raw['amplifier_web/' + name]),
                         'sha256': hashlib.sha256(self.raw['amplifier_web/' + name]).hexdigest(),
                         'capture': 'executed-source-buffer'} for name in execution.NAMES]
        self.nonce = uuid.uuid4().hex + uuid.uuid4().hex
        self.recheck()

    def recheck(self):
        for name, path in self.paths.items():
            if path.is_symlink() or path.read_bytes() != self.raw[name]:
                raise ValueError('Selected test cleanup source changed')

    def evidence(self, ready):
        return {'schema': 1, 'kind': 'qualification-test-checkout',
                'ready': ready, 'readySha256': execution.digest(ready)}


def checkout_cleanup(monkeypatch):
    monkeypatch.setattr(execution, 'Gate', CheckoutGate)


def require_absent(inspection):
    assert inspection['complete'] is True and not inspection['candidates'] and not inspection['protectedIds'], inspection


def require_prior_cleanup(output, profile):
    if profile == 'browser-test':
        prior = json.loads((Path(output) / 'python-project.json').read_text())
        assert prior['cleanupComplete'] is True, 'Previous profile custody is unresolved; inspect original receipts'
