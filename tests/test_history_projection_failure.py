"""Failure evidence must survive real child retirement without private inputs."""
import argparse
import json
from pathlib import Path
import sys

import pytest

from amplifier_web.history_projection_failure import FIELDS, sanitize_failure
from amplifier_web.history_projection_process import ProcessProjector, retry
from amplifier_web.history_projection_worker import MEMORY_LIMIT


@pytest.mark.parametrize('stage', ['setup_store', 'input_decode', 'source_join', 'output_encode'])
async def test_failure_stage_and_memory_survive_worker_retirement(tmp_path, stage):
    owner = ProcessProjector(tmp_path, _command=(sys.executable, str(Path(__file__).resolve()), stage))
    try:
        result = await owner.project({'private': 'never-in-failure-receipt'}, key='private-owner')
        assert result['status'] == 'retry' and result['reason'] == 'memory_limit'
        assert owner._process is None  # Facts were received before retirement.
        facts = result['failure']
        assert facts['stage'] == stage and set(facts) == {'stage', 'exceptionType', 'errno', *FIELDS}
        assert all(value is None or type(value) is int and value >= 0
                   for key, value in facts.items() if key in FIELDS or key == 'errno')
        if sys.platform.startswith('linux'):
            assert facts['residentBytes'] > 0 and facts['virtualBytes'] > 0
            assert facts['peakVirtualBytes'] >= facts['virtualBytes']
            assert facts['addressSpaceLimitBytes'] <= MEMORY_LIMIT
        assert 'private' not in json.dumps(result) and str(tmp_path) not in json.dumps(result)
        assert not list(owner._root.rglob('*.input')) and not list(owner._root.rglob('*.output'))
    finally:
        await owner.close()


def test_untrusted_failure_fields_are_bounded_and_content_free():
    value = {'stage': 'source_join', 'residentBytes': True, 'virtualBytes': -1,
             'peakVirtualBytes': 2**100, 'peakResidentBytes': 123,
             'path': '/private', 'exception': 'private message'}
    assert sanitize_failure(value) == {'stage': 'source_join',
        'residentBytes': None, 'virtualBytes': None, 'peakVirtualBytes': None,
        'peakResidentBytes': 123, 'addressSpaceLimitBytes': None, 'exceptionType': None, 'errno': None}
    for stage in ['private-value', [], None]:
        assert retry('memory_limit', {'stage': stage}) == {'status': 'retry', 'reason': 'memory_limit'}


@pytest.mark.parametrize('stage,kind,reason,number', [
    ('setup_policy', 'OSError', 'worker_failed', 5),
    ('setup_import', 'ImportError', 'worker_failed', None),
    ('setup_store', 'PermissionError', 'worker_failed', 13),
    ('setup_store', 'ValueError', 'worker_failed', None),
    ('setup_store', 'MemoryError', 'memory_limit', None),
])
async def test_setup_failure_classification_survives_real_child_retirement(tmp_path, stage, kind, reason, number):
    owner = ProcessProjector(tmp_path, _command=(sys.executable, str(Path(__file__).resolve()),
        stage, '--failure', kind))
    try:
        result = await owner.project({'private': 'never publish'}, key='private-owner')
        assert result['status'] == 'retry' and result['reason'] == reason
        assert owner._process is None
        facts = result['failure']
        assert (facts['stage'], facts['exceptionType'], facts['errno']) == (stage, kind, number)
        assert 'private' not in json.dumps(result) and str(tmp_path) not in json.dumps(result)
    finally:
        await owner.close()


def test_exception_diagnostics_accept_only_fixed_types_and_bounded_errno():
    for kind, number in [('private-class-name', 2**80), ([], True), (None, -1)]:
        value = sanitize_failure({'stage': 'setup_store', 'exceptionType': kind, 'errno': number})
        assert value['exceptionType'] is None and value['errno'] is None
    assert sanitize_failure({'stage': 'setup_store', 'exceptionType': 'PermissionError', 'errno': 13})['errno'] == 13


if __name__ == '__main__':
    from amplifier_web import history_projection_worker as worker
    parser = argparse.ArgumentParser()
    parser.add_argument('stage')
    parser.add_argument('--failure', default='MemoryError')
    parser.add_argument('--spool', required=True)
    parser.add_argument('--cache-home', required=True)
    parser.add_argument('--memory-limit', type=int, required=True)
    args = parser.parse_args()
    def fail():
        import builtins
        kind = getattr(builtins, args.failure)
        if issubclass(kind, OSError):
            raise kind(13 if kind is PermissionError else 5, 'private path and message')
        raise kind('private path and message')
    class Model:
        def __init__(self, home):
            if args.stage == 'setup_store':
                fail()
        def project(self, inputs, *, check_budget):
            if args.stage == 'source_join':
                check_budget.stage('source_join')
                check_budget.stage('unknown private label')  # Cannot grow label space.
                raise worker.MemoryLimit()
            return {'status': 'complete', 'tree': {}}
    if args.stage == 'input_decode':
        original = worker.json.loads
        def fail_input(value, *args, **kwargs):
            if isinstance(value, bytes) and b'never-in-failure-receipt' in value:
                raise MemoryError()
            return original(value, *args, **kwargs)
        worker.json.loads = fail_input
    elif args.stage == 'output_encode':
        def fail_output(*args, **kwargs):
            raise MemoryError()
        worker.write_json = fail_output
    factory = Model
    if args.stage == 'setup_policy':
        worker.memory_policy = lambda limit: fail()
    elif args.stage == 'setup_import':
        import builtins
        original_import = builtins.__import__
        def importing(name, *args, **kwargs):
            if name == 'history_read_model':
                fail()
            return original_import(name, *args, **kwargs)
        builtins.__import__ = importing
        factory = None
    worker.run(args.spool, args.cache_home, model_factory=factory, memory_limit=args.memory_limit)
