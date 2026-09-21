"""Measurement verdicts cannot turn failed/missing receipts into success."""
import importlib.util
import json
from pathlib import Path
import sys

SOURCE = Path(__file__).resolve().parents[1] / 'scripts/work_profile'
sys.path.insert(0, str(SOURCE))
spec = importlib.util.spec_from_file_location('tool_benchmark', SOURCE/'tool_benchmark.py')
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


def test_direct_verdict_requires_real_three_calls_and_exact_result():
    history = {'messages':[]}
    for name in benchmark.FILES:
        history['messages'].extend([
            {'role':'assistant','tool_calls':[{'name':'bash','arguments':{'command':'cat '+name}}]},
            {'role':'tool','name':'bash','content':json.dumps({'success':True,'output':{'stdout':'fixture'}})}])
    good = benchmark.summarize('direct', history, json.dumps(benchmark.EXPECTED))
    assert good['correctOutput'] and good['matchesRequestedMode'] and good['topLevelCallCount'] == 3
    wrong = benchmark.summarize('direct', history, '{"ids":["a","b","c"],"sum":50}')
    assert not wrong['correctOutput']
    history['messages'].pop(); history['messages'].pop()
    assert not benchmark.summarize('direct', history, json.dumps(benchmark.EXPECTED))['matchesRequestedMode']


def test_programmatic_partial_failure_is_not_success_even_with_correct_printed_text():
    result = {'success':False,'output':{'calls':[{'success':True},{'success':True},{'success':False}]}}
    history = {'messages':[{'role':'assistant','tool_calls':[{'name':'tool_exec','arguments':{'code':'text("correct-looking")'}}]},
                           {'role':'tool','name':'tool_exec','content':json.dumps(result)}]}
    measured = benchmark.summarize('programmatic', history, json.dumps(benchmark.EXPECTED))
    assert measured['correctOutput'] and measured['nestedCallCount'] == 3
    assert not measured['matchesRequestedMode'] and not measured['successfulToolResults']


def test_credential_cleanup_only_generated_text_and_readonly_sqlite(tmp_path):
    secret = 'synthetic-not-a-real-credential'
    config = tmp_path/'settings.yaml'; config.write_text('api_key: '+secret)
    database = tmp_path/'app.sqlite3'; database.write_bytes(b'unchanged-binary '+secret.encode())
    report = benchmark.clean_credentials(tmp_path, {'config':{'api_key':secret}})
    assert secret not in config.read_text()
    assert database.read_bytes() == b'unchanged-binary '+secret.encode()
    assert report['redactedFiles'] == 1 and report['sqliteMatches'] == report['remainingMatches'] == 1
    assert secret not in json.dumps(report)
