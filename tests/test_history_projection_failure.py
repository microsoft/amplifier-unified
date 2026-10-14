"""Failure evidence must survive real child retirement without private inputs."""
import argparse
import json
from pathlib import Path
import sys

import pytest

from amplifier_web.history_projection_failure import FIELDS, sanitize_failure
from amplifier_web.history_projection_process import ProcessProjector, retry
from amplifier_web.history_projection_worker import MEMORY_LIMIT


@pytest.mark.parametrize('stage', ['setup', 'input_decode', 'source_join', 'output_encode'])
async def test_failure_stage_and_memory_survive_worker_retirement(tmp_path, stage):
    owner = ProcessProjector(tmp_path, _command=(sys.executable, str(Path(__file__).resolve()), stage))
    try:
        result = await owner.project({'private': 'never-in-failure-receipt'}, key='private-owner')
        assert result['status'] == 'retry' and result['reason'] == 'memory_limit'
        assert owner._process is None  # Facts were received before retirement.
        facts = result['failure']
        assert facts['stage'] == stage and set(facts) == {'stage', *FIELDS}
        assert all(value is None or type(value) is int and value >= 0
                   for key, value in facts.items() if key != 'stage')
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
        'peakResidentBytes': 123, 'addressSpaceLimitBytes': None}
    for stage in ['private-value', [], None]:
        assert retry('memory_limit', {'stage': stage}) == {'status': 'retry', 'reason': 'memory_limit'}


if __name__ == '__main__':
    from amplifier_web import history_projection_worker as worker
    parser = argparse.ArgumentParser()
    parser.add_argument('stage')
    parser.add_argument('--spool', required=True)
    parser.add_argument('--cache-home', required=True)
    parser.add_argument('--memory-limit', type=int, required=True)
    args = parser.parse_args()
    class Model:
        def __init__(self, home):
            if args.stage == 'setup':
                raise MemoryError('never emit exception contents')
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
    worker.run(args.spool, args.cache_home, model_factory=Model, memory_limit=args.memory_limit)
