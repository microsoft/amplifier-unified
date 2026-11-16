"""Exercise an installed wheel without importing the application or native runtime."""
import asyncio
import importlib.metadata as metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

from amplifier_status import Publisher, discover


def test_installed_import_and_entry_points_need_no_host(tmp_path):
    # A fresh isolated interpreter prevents the checkout or earlier tests from
    # satisfying imports. Block native/application imports even if installed.
    script = '''
import importlib.abc
import importlib.metadata as metadata
import sys
class NoHost(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'amplifier_core', 'amplifier_foundation', 'amplifier_web'}:
            raise AssertionError('Standalone import tried to load ' + fullname)
sys.meta_path.insert(0, NoHost())
import amplifier_status
dist = metadata.distribution('amplifier-status-emitter')
assert not dist.requires
assert dist.version == amplifier_status.__version__
assert amplifier_status.__amplifier_module_type__ == 'hook'
entries = {(entry.group, entry.name): entry for entry in dist.entry_points}
cli = entries['console_scripts', 'amplifier-status']
hook = entries['amplifier.modules', 'hooks-status-emitter']
assert cli.value == 'amplifier_status.cli:main' and callable(cli.load())
assert hook.value == 'amplifier_status.hook:mount'
assert hook.load() is amplifier_status.mount
assert {str(path) for path in dist.files if str(path).startswith('amplifier_status/') and str(path).endswith('.py')} == {
    'amplifier_status/' + name + '.py'
    for name in ('__init__', 'cli', 'hook', 'publisher', 'reader', 'schema')}
'''
    subprocess.run([sys.executable, '-I', '-c', script], cwd=tmp_path,
                   check=True, capture_output=True, text=True, timeout=15)


def test_installed_publisher_reader_and_cli_preserve_observation_boundary(tmp_path):
    directory = tmp_path / 'observations'
    publisher = Publisher(directory, host='standalone', version='test',
                          instance_id='fixture-instance', max_sessions=1,
                          heartbeat=.05, coalesce=.01)
    try:
        assert publisher.submit('session', {
            'now': {'status': 'tool', 'tools': [{'id': 'call', 'name': 'bash', 'arguments': 'PRIVATE'}]},
            'planItems': [{'content': 'PRIVATE', 'status': 'in_progress'}],
            'messages': ['PRIVATE'], 'draft': 'PRIVATE'})
        assert not publisher.submit('overflow', {})
        deadline = time.monotonic() + 5
        while True:
            report = discover(directory, instance=publisher.instance_id)
            if report['sessions'] and report['sessions'][0]['coverage']['omittedSubmissions'] == 1:
                break
            assert time.monotonic() < deadline, report
            time.sleep(.01)
        assert not report['complete'] and not report['truncated'] and report['invalid'] == 0
        assert len(report['sessions']) == 1
        row = report['sessions'][0]
        assert row['producer']['instanceId'] == publisher.instance_id
        assert row['where']['sessionId'] == 'session' and row['now']['status'] == 'tool'
        assert row['plan']['counts'] == {'pending': 0, 'in_progress': 1, 'completed': 0}
        assert 'PRIVATE' not in json.dumps(report)
        assert discover(directory, instance='other-instance')['sessions'] == []
        future = row['observedAt'] + row['staleAfterSeconds'] + 60
        assert discover(directory, instance=publisher.instance_id, now=future)['sessions'][0]['freshness'] == 'stale'
    finally:
        publisher.close()
    assert not publisher._thread.is_alive()
    ended = discover(directory, instance=publisher.instance_id)['sessions'][0]
    assert ended['lifecycle'] == ended['freshness'] == 'ended'
    command = Path(sys.executable).parent / ('amplifier-status.exe' if os.name == 'nt' else 'amplifier-status')
    result = subprocess.run([str(command), '--directory', str(directory), '--instance', publisher.instance_id],
                            cwd=tmp_path, check=True, capture_output=True, text=True, timeout=15)
    observed = json.loads(result.stdout)
    assert observed['scope'] == 'instance' and not observed['complete']
    assert [(row['producer']['instanceId'], row['where']['sessionId'], row['lifecycle'])
            for row in observed['sessions']] == [('fixture-instance', 'session', 'ended')]
    assert 'PRIVATE' not in result.stdout


def test_optional_hook_defers_to_existing_host_without_starting_publication():
    dist = metadata.distribution('amplifier-status-emitter')
    hook = next(entry for entry in dist.entry_points
                if entry.group == 'amplifier.modules' and entry.name == 'hooks-status-emitter')
    # No session, event bus or publisher is provided: the host-owned branch
    # must return before attempting to create or control another observer.
    result = asyncio.run(hook.load()(SimpleNamespace(config={'application_host': 'Amplifier Unified'})))
    assert result == {'name': 'hooks-status-emitter', 'status': 'host-owned'}
