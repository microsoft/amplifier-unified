"""Explicit run-local TEST adapter, never installed with amplifier_web.

Standard reviewed-code trust, NOT a hostile-candidate sandbox or nonforgeable
Python capability. No official-delivery descriptor or production eligibility is
created. The host's effect permit callback and transport remain unmodified.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
import csv
import hashlib
import importlib
import importlib.metadata
import io
import json
import os
from pathlib import Path
import sys
import uuid
import zipfile

from candidate_native_protocol import KIND, digest, require, sha

MEMBERS = ('environment_cleanup_launcher.py', 'test_environment_worker.py', 'environment_retention.py')
# Include producers, initializer, contract and cleanup seam, not just the loader.
SOURCES = (*MEMBERS, 'environment_cleanup_execution.py', 'task_environments.py',
           'test_environment_profiles.py', 'test_environment_preparation.py',
           'test_environment_initialize_guest.py', 'test_environment_prepare_guest.py',
           'test_job_guest.py', 'test_job_transport.py', 'test_source_transfer.py')


class WheelInputs:
    """One bounded immutable capture; hash and ZIP selection use the SAME bytes."""
    def __init__(self, wheel, selected_digest, source_blobs, installed):
        wheel, installed = Path(wheel), Path(installed).resolve()
        require(sha(selected_digest) and not wheel.is_symlink() and wheel.stat().st_size <= 128 * 1024 ** 2,
                'Invalid candidate wheel binding')
        with wheel.open('rb') as stream:
            raw = stream.read(128 * 1024 ** 2 + 1)
        require(len(raw) <= 128 * 1024 ** 2 and hashlib.sha256(raw).hexdigest() == selected_digest,
                'Candidate wheel changed')
        self.sha256, self.installed, self.wheel = selected_digest, installed, wheel
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            names = archive.namelist()
            require(len(names) == len(set(names)), 'Duplicate wheel member')
            record, = [name for name in names if name.endswith('.dist-info/RECORD')]
            rows = list(csv.reader(io.StringIO(archive.read(record).decode())))
            records = {row[0]: row[1:] for row in rows}
            require(len(rows) == len(records), 'Duplicate RECORD member')
            self.raw = {}
            for name in SOURCES:
                member = 'amplifier_web/' + name
                info = archive.getinfo(member)
                require(info.file_size <= 262144, 'Oversized selected source')
                body = archive.read(member)
                record_hash = 'sha256=' + base64.urlsafe_b64encode(hashlib.sha256(body).digest()).decode().rstrip('=')
                require(records.get(member) == [record_hash, str(len(body))], 'Wheel RECORD mismatch')
                blob = hashlib.sha1(b'blob ' + str(len(body)).encode() + b'\0' + body).hexdigest()
                require(source_blobs.get(member) == blob, 'Candidate source/tree/member mismatch')
                target = installed / member
                require(not target.is_symlink() and target.resolve().is_relative_to(installed)
                        and target.read_bytes() == body, 'Installed member differs from candidate wheel')
                self.raw[member] = body
        self.origins = {}
        self.recheck()

    def recheck(self):
        # Actual current import origins, including already imported modules, must
        # point to these installed bytes, never a selected workload source tree.
        try:
            dist = importlib.metadata.distribution('amplifier-unified')
        except importlib.metadata.PackageNotFoundError as exc:
            raise ValueError('Candidate distribution origin is unavailable') from exc
        require(Path(dist.locate_file('amplifier_web')).resolve() == self.installed / 'amplifier_web',
                'Candidate distribution origin changed')
        direct = json.loads(dist.read_text('direct_url.json') or '{}')
        require(direct.get('url') == self.wheel.resolve().as_uri()
                and not direct.get('dir_info') and not direct.get('vcs_info'), 'Candidate install is not the selected wheel')
        for member, body in self.raw.items():
            module = importlib.import_module(member.removesuffix('.py').replace('/', '.'))
            path = Path(module.__file__)
            require(not path.is_symlink() and path.resolve() == self.installed / member
                    and path.read_bytes() == body, 'Candidate module origin or selected buffer changed')
            self.origins[member] = {'path': str(path), 'sha256': hashlib.sha256(body).hexdigest()}
        require(Path(sys.prefix).resolve() in self.installed.parents, 'Candidate interpreter/prefix changed')
        from amplifier_web.application_generations import bootstrap_identity
        current = bootstrap_identity(sys.prefix)
        if hasattr(self, 'installation'):
            require(current == self.installation, 'Candidate installation binding changed')
        self.installation = current


class CandidateTestGate:
    """Separate TEST authority. Reuse production transport *without mutation*."""
    def __init__(self, inputs, registry, resource):
        from amplifier_web import environment_cleanup_execution as transport
        self.inputs, self.registry, self.resource = inputs, registry, resource
        self.raw = {name: body for name, body in inputs.raw.items() if name.rsplit('/', 1)[1] in MEMBERS}
        self.members = [{'name': 'amplifier_web/' + name, 'bytes': len(self.raw['amplifier_web/' + name]),
                        'sha256': hashlib.sha256(self.raw['amplifier_web/' + name]).hexdigest(),
                        'capture': 'executed-source-buffer'} for name in transport.NAMES]
        self.nonce = uuid.uuid4().hex + uuid.uuid4().hex
        self.recheck()

    def bootstrap(self):
        from amplifier_web.environment_cleanup_execution import Gate
        return Gate.bootstrap(self)

    def receive(self, process, seconds=20):
        from amplifier_web.environment_cleanup_execution import Gate
        return Gate.receive(self, process, seconds)

    def validate(self, ready, process, python):
        from amplifier_web.environment_cleanup_execution import Gate
        return Gate.validate(self, ready, process, python)

    def recheck(self):
        self.registry.recheck(self.resource, action='remove')
        self.inputs.recheck()

    def evidence(self, ready):
        # Deliberately NOT the production serving/qualification/artifact shape.
        return {'kind': KIND, 'schema': 1, 'selectionDigest': digest(self.registry.selection),
                'wheelSha256': self.inputs.sha256, 'installationBinding': self.inputs.installation,
                'resource': self.resource, 'ready': ready, 'readySha256': digest(ready)}


@contextmanager
def candidate_test_cleanup(app, inputs, registry, resource):
    """Bounded pytest adapter for exactly this app, resource and private seam.

    All journal, source, backend, owner/revision, Stop and durable/final permit
    guards remain in the actual candidate effect()/invoke(). Other apps still
    call the original production adapter. Restoration is unconditional.
    """
    from amplifier_web import task_environments as owner
    original = owner.invoke_cleanup
    def invoke_cleanup(target, config, row, action, args, permit):
        if target is not app:
            return original(target, config, row, action, args, permit)
        require(row['id'] == resource and action in ('remove', 'finishCleanup')
                and config.get('backend', 'docker') == 'docker' and not config.get('remoteRunner'),
                'Wrong TEST adapter owner/action/backend')
        entry = registry.value['entries'][-1]
        plan = entry['plan']
        require(row['sessionId'] == plan['owner'] and row['launchOperationId'] == plan['launch']
                and row['universeId'] == plan['universe'] and row['source'] == plan['source']
                and row['backendIdentity'] == plan['backend']
                and row['snapshot'] == entry.get('snapshot')
                and str(owner.storage.directory(app.data_dir, row)) == plan['root'], 'Changed TEST owner binding')
        gate = CandidateTestGate(inputs, registry, resource)
        return owner.invoke(config, app.data_dir, row, action, args, _cleanup_gate=gate, _cleanup_permit=permit)
    owner.invoke_cleanup = invoke_cleanup
    try:
        yield
    finally:
        require(owner.invoke_cleanup is invoke_cleanup, 'Concurrent TEST adapter replacement')
        owner.invoke_cleanup = original
