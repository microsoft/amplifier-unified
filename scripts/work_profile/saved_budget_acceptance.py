"""Inspect copied saved budget evidence without constructing an execution host."""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path

from acceptance import private_json


RECEIPT_FIELDS = ('id', 'revision', 'producerId', 'budgetRevision', 'admittedAt',
                  'sessionId', 'rootSessionId', 'kind', 'provider', 'model', 'phase',
                  'startedAt', 'endedAt', 'usage', 'lifecycle')
USAGE_FIELDS = ('inputTokens', 'outputTokens', 'totalTokens', 'cacheReadTokens',
                'cacheWriteTokens', 'reasoningTokens', 'costUsd', 'costType')


def accounting_fixture(view, control):
    """Copy only accounting fields: no canonical paths, tools, or runtime config."""
    execution = {}
    for name in ('nodes', 'retiredUsageNodes'):
        execution[name] = []
        for row in view.get('execution', {}).get(name, []):
            if row.get('kind') != 'llm':
                continue
            safe = {key: row[key] for key in RECEIPT_FIELDS if key in row}
            safe['usage'] = {key: row['usage'][key] for key in USAGE_FIELDS if key in row.get('usage', {})}
            execution[name].append(safe)
    return {'session': {'id': view['id'], 'execution': execution,
                        'workers': [{'sessionId': row['sessionId']} for row in view.get('workers', []) if row.get('sessionId')]},
            'budget': control['capacity'], 'denial': control.get('capacityLastDenial')}


def project(fixture):
    from amplifier_web.capacity import usage_snapshot, evaluate
    usage = usage_snapshot(fixture['session'])
    policy = dict(fixture['budget'])
    if fixture.get('denial') and fixture['denial'].get('budgetRevision') == policy['revision']:
        policy['lastDenial'] = fixture['denial']
    return {'budget': policy, 'usage': usage, 'admission': evaluate(policy, usage)}


def fingerprints(paths):
    return {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths if path.is_file()}


async def run(args):
    folder, target = args.run.resolve(), args.output.resolve()
    if not (folder / 'report.json').exists() or not (folder / 'capacity.json').exists():
        raise ValueError('A completed live budget probe and pre-limit receipt are required')
    baseline = json.loads((folder/'capacity.json').read_text())
    sid = baseline['usage']['sessionId']
    views = list((folder/'shared/projects').glob(f'*/sessions/{sid}/unified/view.json'))
    if len(views) != 1 or not views[0].resolve().is_relative_to(folder):
        raise ValueError('Exactly one saved native view within this fixture is required')
    control_path = folder/'app/sessions'/sid/'control-state.json'
    native = views[0].parent.parent
    evidence = [folder/name for name in ('report.json', 'capacity.json', 'events.json', 'app/app.sqlite3')]
    evidence += [control_path, views[0], *[native/name for name in ('transcript.jsonl', 'transcript.jsonl.backup', 'metadata.json', 'metadata.json.backup')]]
    before = fingerprints(evidence)
    target.mkdir(mode=0o700, parents=True, exist_ok=False)
    fixture = accounting_fixture(json.loads(views[0].read_text()), json.loads(control_path.read_text()))
    private_json(target/'accounting-fixture.json', fixture)
    # Re-read the copied fixture. No application, provider, scheduler or runtime
    # is created, and no copied path can point a writer at the original history.
    after = project(json.loads((target/'accounting-fixture.json').read_text()))
    events = json.loads((folder/'events.json').read_text())
    report = {'schema_version':2, 'source_run':folder.name, 'provider_calls':0,
              'execution':'disabled; no execution host constructed',
              'projection':'shared capacity.usage_snapshot/evaluate over copied persisted receipts', 'checks':{}}
    checks = report['checks']
    checks['baseline_real_admitted_call'] = baseline['usage']['calls'] == 1 and all(row.get('admittedAt') and row.get('budgetRevision') == 0 for row in baseline['usage']['receipts'])
    checks['budget_policy_persisted'] = after['budget']['enabled'] and after['budget']['maxTotalTokens'] == 1 and after['budget']['revision'] == 1
    checks['rejection_receipt_persisted'] = fixture['denial']['allowed'] is False and fixture['denial']['budgetRevision'] == 1
    checks['no_second_provider_call'] = after['usage']['calls'] == baseline['usage']['calls'] == 1
    checks['limit_visibly_blocks_admission'] = after['admission']['allowed'] is False and any('limit reached' in reason for reason in after['admission']['reasons'])
    checks['failure_publicly_observed'] = any(e['kind'] == 'runtime.error' and e.get('event') == 'provider.error' for e in events)
    checks['no_fabricated_model_reply'] = not any(e['kind'] == 'assistant.message' and 'BUDGET_SHOULD_NOT_RUN' in e.get('text','') for e in events)
    reported = {key: baseline['usage']['receipts'][0]['usage'][key] for key in USAGE_FIELDS if key in baseline['usage']['receipts'][0]['usage']}
    current = after['usage']['receipts'][0]['usage']
    checks['original_reported_counters_preserved'] = all(current.get(key) == value for key, value in reported.items())
    gross_input = reported['inputTokens'] + reported.get('cacheWriteTokens', 0)
    gross_total = gross_input + reported['outputTokens']
    checks['cache_writes_counted_once_reads_not_added_again'] = current['grossInputTokens'] == gross_input and current['grossTotalTokens'] == gross_total
    checks['budget_consumes_inclusive_total'] = after['usage']['metrics']['grossTotalTokens']['value'] == gross_total
    checks['original_evidence_unchanged'] = before == fingerprints(evidence)
    report.update(provider_reported_usage=reported,
                  inclusive_usage={key: current[key] for key in ('grossInputTokens', 'grossTotalTokens')},
                  original_evidence_files=len(before),
                  usage_provenance='Provider-adapter normalized counters; raw API payload was not collected.',
                  accounting_audit='Resolved: inclusive budget adds reported cache writes once; normalized counters and original live reports remain unchanged.',
                  validation_boundary='Passive saved-receipt projection only; browser and execution-boundary regressions are separate evidence.')
    report['passed'] = all(checks.values())
    private_json(target/'report.json', report)
    print(json.dumps(report), flush=True)
    return report['passed']


def main():
    os.umask(0o077)
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args=parser.parse_args()
    raise SystemExit(0 if asyncio.run(run(args)) else 1)


if __name__=='__main__':
    main()
