"""Inspect a stopped synthetic budget probe without restarting a provider."""
import argparse
import asyncio
import json
import os
from pathlib import Path

from acceptance import private_json


async def run(args):
    from amplifier_web.server import create_app
    folder, target = args.run.resolve(), args.output.resolve()
    if not (folder / 'report.json').exists() or not (folder / 'capacity.json').exists():
        raise ValueError('A completed live budget probe and pre-limit receipt are required')
    target.mkdir(mode=0o700, parents=True, exist_ok=False)
    os.environ.update(AMPLIFIER_HOME=str(folder/'shared'), AMPLIFIER_WEB_HOME=str(folder/'app'),
                      AMPLIFIER_SESSION_STATE_HOME=str(folder/'ownership'))
    app = await create_app(folder/'app', workspace=folder/'workspace', runtime=False,
                           voice=False, preload_providers=False, background_updates=False)
    service = app['service']
    report = {'schema_version':1, 'source_run':folder.name, 'provider_calls':0, 'execution':'disabled', 'checks':{}}
    try:
        baseline = json.loads((folder/'capacity.json').read_text())
        sid = baseline['usage']['sessionId']
        after = (await service.dispatch('capacity.read', {'sessionId':sid}))['result']
        control = json.loads((folder/'app/sessions'/sid/'control-state.json').read_text())
        events = json.loads((folder/'events.json').read_text())
        checks = report['checks']
        checks['baseline_real_admitted_call'] = baseline['usage']['calls'] == 1 and all(row.get('admittedAt') and row.get('budgetRevision') == 0 for row in baseline['usage']['receipts'])
        checks['budget_policy_persisted'] = after['budget']['enabled'] and after['budget']['maxTotalTokens'] == 1 and after['budget']['revision'] == 1
        checks['rejection_receipt_persisted'] = control['capacityLastDenial']['allowed'] is False and control['capacityLastDenial']['budgetRevision'] == 1
        checks['no_second_provider_call'] = after['usage']['calls'] == baseline['usage']['calls'] == 1
        checks['limit_visibly_blocks_admission'] = after['admission']['allowed'] is False and any('limit reached' in reason for reason in after['admission']['reasons'])
        checks['failure_publicly_observed'] = any(e['kind'] == 'runtime.error' and e.get('event') == 'provider.error' for e in events)
        checks['no_fabricated_model_reply'] = not any(e['kind'] == 'assistant.message' and 'BUDGET_SHOULD_NOT_RUN' in e.get('text','') for e in events)
        report['provider_reported_usage'] = baseline['usage']['receipts'][0]['usage']
        report['usage_limitation'] = 'Provider totalTokens excludes a separately reported cacheWriteTokens value in this receipt; host preserves both and does not invent inclusive total semantics.'
        report['passed'] = all(checks.values())
    finally:
        await service.close()
        private_json(target/'report.json',report)
    print(json.dumps(report),flush=True)
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
