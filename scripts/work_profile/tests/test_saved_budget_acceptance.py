import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace


async def test_passive_reviewer_uses_copied_counters_and_never_opens_original_app(tmp_path, monkeypatch):
    scripts = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location('saved_budget_acceptance', scripts/'saved_budget_acceptance.py')
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    source, target = tmp_path/'source', tmp_path/'review'
    native = source/'shared/projects/synthetic/sessions/root'
    (native/'unified').mkdir(parents=True)
    (source/'app/sessions/root').mkdir(parents=True)
    # A real AppService cannot open this file. The passive reviewer must only
    # hash it, and must never import/reopen an execution host or its settings.
    (source/'app/app.sqlite3').write_bytes(b'original database evidence')
    usage = {'inputTokens': 3, 'outputTokens': 7, 'totalTokens': 10,
             'cacheWriteTokens': 12635, 'cacheReadTokens': 0, 'costUsd': .0316775, 'costType': 'reported'}
    receipt = {'id': 'call', 'sessionId': 'root', 'rootSessionId': 'root', 'kind': 'llm',
               'phase': 'completed', 'admittedAt': 1, 'budgetRevision': 0, 'revision': 2,
               'usage': usage, 'output': 'unrelated original content'}
    values = {
        source/'report.json': {'original': True},
        source/'capacity.json': {'usage': {'sessionId': 'root', 'calls': 1, 'receipts': [receipt]}},
        source/'events.json': [{'kind': 'runtime.error', 'event': 'provider.error'}],
        source/'app/sessions/root/control-state.json': {
            'capacity': {'enabled': True, 'maxTotalTokens': 1, 'revision': 1},
            'capacityLastDenial': {'allowed': False, 'budgetRevision': 1, 'reasons': ['limit reached']}},
        native/'unified/view.json': {'id': 'root', 'workspace': str(source),
                                    'messages': ['unrelated original content'],
                                    'nativeIdentity': {'path': str(native)},
                                    'execution': {'nodes': [receipt]}},
    }
    for path, value in values.items():
        path.write_text(json.dumps(value))
    (native/'transcript.jsonl').write_text('original transcript bytes\n')
    originals = {path: path.read_bytes() for path in source.rglob('*') if path.is_file()}
    assert await helper.run(SimpleNamespace(run=source, output=target))
    report = json.loads((target/'report.json').read_text())
    assert report['provider_calls'] == 0 and all(report['checks'].values())
    assert report['provider_reported_usage']['totalTokens'] == 10
    assert report['inclusive_usage'] == {'grossInputTokens': 12638, 'grossTotalTokens': 12645}
    copied = (target/'accounting-fixture.json').read_text()
    assert str(source) not in copied and 'unrelated original content' not in copied
    assert all(path.read_bytes() == original for path, original in originals.items())
