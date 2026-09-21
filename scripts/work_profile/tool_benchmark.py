"""Opt-in matched real-provider direct/programmatic tool comparison; one run each.

Only synthetic files and fresh private sessions. All observed provider usage,
including prompts and schemas, is included. This is a single ordered observation,
not a general latency, billing or speedup claim; do not rerun to select a winner.
"""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import subprocess
import time

import yaml
from acceptance import Observation, installed_revisions, private_json, setup
from parity_acceptance import profile

FILES = ('fixture-a.json', 'fixture-b.json', 'fixture-c.json')
EXPECTED = {'ids': ['a', 'b', 'c'], 'sum': 51}
COMMON = (
    'Perform this bounded synthetic read-only comparison task. Read fixture-a.json, fixture-b.json and fixture-c.json '
    'from the current workspace exactly once each. Each JSON object has id, value and supplementary details. '
    'Return the ids in filename order and sum their value fields. Your final response must contain only compact '
    'JSON with keys ids and sum, followed by BENCHMARK_DONE. Do not delegate, create goals, schedule, browse, '
    'write files, or call unrelated tools. Do not retry a tool that fails; report that failure and BENCHMARK_DONE. '
    'Use the mounted tool schemas already supplied to you. '
)
MODES = {
    'direct': 'Execution mode: direct. Make exactly three separate bash calls, one per file, with commands cat fixture-a.json, cat fixture-b.json and cat fixture-c.json. Do not use tool_exec or combine commands.',
    'programmatic': 'Execution mode: programmatic. Make one tool_exec call. In that JavaScript, use Promise.all to call tools.bash three times, with commands cat fixture-a.json, cat fixture-b.json and cat fixture-c.json. Each successful reply.output is an object with stdout; parse that stdout as JSON. Publish only the requested ids and sum using text(). Keep the authoritative call receipts. Do not invoke bash directly outside tool_exec.',
}


def calls_and_results(history):
    calls, results = [], []
    for message in history.get('messages', []):
        for call in message.get('tool_calls') or []:
            name = call.get('tool') or call.get('name') or call.get('function', {}).get('name')
            args = call.get('arguments') or call.get('function', {}).get('arguments', {})
            if isinstance(args, str):
                try: args = json.loads(args)
                except ValueError: args = {}
            calls.append({'name': name, 'arguments': args})
        if message.get('role') == 'tool':
            content = message.get('content')
            try: value = json.loads(content) if isinstance(content, str) else content
            except ValueError: value = None
            results.append({'name': message.get('name'), 'result': value,
                            'serializedBytes': len((content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)).encode())})
    return calls, results


def correct(text):
    decoder = json.JSONDecoder()
    for match in re.finditer(r'\{', text):
        try:
            value, _ = decoder.raw_decode(text[match.start():])
            if value == EXPECTED: return True
        except ValueError: pass
    return False


def summarize(mode, history, final):
    calls, results = calls_and_results(history)
    programs = [r['result'] for r in results if r['name'] == 'tool_exec' and isinstance(r['result'], dict)]
    nested = [c for result in programs for c in (result.get('output') or {}).get('calls', [])]
    direct = [c for c in calls if c['name'] == 'bash']
    expected_commands = sorted('cat ' + name for name in FILES)
    direct_match = sorted(c['arguments'].get('command', '') for c in direct) == expected_commands
    program_match = (len(programs) == 1 and programs[0].get('success') is True and len(nested) == 3
                     and all(row.get('success') is True and row.get('name') == 'bash' for row in nested))
    return {'correctOutput': correct(final), 'topLevelCalls': calls,
            'topLevelCallCount': len(calls), 'nestedCallCount': len(nested),
            'nestedReceipts': nested, 'modelVisibleToolResultBytes': sum(r['serializedBytes'] for r in results),
            'matchesRequestedMode': (direct_match and len(calls) == 3) if mode == 'direct' else
                                    (program_match and len(calls) == 1 and calls[0]['name'] == 'tool_exec'),
            'successfulToolResults': all(isinstance(r['result'], dict) and r['result'].get('success') is True for r in results)}


def source_inventory(args):
    root = args.module_root.resolve()
    sources = {'host': Path(__file__).resolve().parents[2], 'work': args.bundle.resolve().parent,
               'loop': root/'worktrees/parity-loop-integration', 'context': root/'worktrees/parity-context-checkpoints',
               'bash': root/'worktrees/parity-managed-process', 'web': root/'worktrees/parity-truthful-web',
               'tool-exec': root/'repos/amplifier-module-tool-exec', 'foundation':root/'worktrees/parity-foundation-current'}
    result = {}
    for name, path in sources.items():
        head = subprocess.check_output(['git','-C',str(path),'rev-parse','HEAD'], text=True).strip()
        diff = subprocess.check_output(['git','-C',str(path),'diff','HEAD','--'])
        result[name] = {'commit':head, 'trackedDirty':bool(diff), 'trackedDiffSha256':hashlib.sha256(diff).hexdigest()}
    result['harnessSha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    return result


def clean_credentials(folder, provider):
    """After shutdown, redact this run's generated text only; inspect SQLite only."""
    secrets = set()
    def collect(value, key=''):
        if isinstance(value, dict):
            for key, child in value.items(): collect(child, key)
        elif isinstance(value, list):
            for child in value: collect(child, key)
        elif isinstance(value, str) and len(value) > 12 and re.search(r'api.?key|secret|password|authorization|access.?token|refresh.?token', key, re.I):
            secrets.add(value.encode())
    collect(provider)
    report = {'scannedFiles': 0, 'redactedFiles': 0, 'remainingMatches': 0, 'sqliteMatches': 0}
    skip = {'cache', 'runtime', 'foundation', 'node_modules', '.git', '.venv', '__pycache__', 'shell-packages'}
    for root, directories, names in os.walk(folder, followlinks=False):
        directories[:] = [n for n in directories if n not in skip and not (Path(root) / n).is_symlink()]
        for name in names:
            path = Path(root) / name
            if path.is_symlink() or not (path.suffix in {'.json', '.jsonl', '.yaml', '.yml', '.txt', '.log', '.md'} or name.startswith('app.sqlite3')): continue
            report['scannedFiles'] += 1
            data = path.read_bytes()
            matches = sum(data.count(secret) for secret in secrets)
            if not matches: continue
            if name.startswith('app.sqlite3'):
                report['sqliteMatches'] += matches
            else:
                for secret in secrets: data = data.replace(secret, b'[REDACTED]')
                path.write_bytes(data); report['redactedFiles'] += 1
            report['remainingMatches'] += sum(data.count(secret) for secret in secrets)
    return report


async def run(args):
    from aiohttp import web
    import amplifier_web.runtime_worker as worker
    from amplifier_web.server import create_app
    from amplifier_web.session_client import SessionClient
    from amplifier_web.capacity import usage_snapshot

    folder, provider = setup(args)
    bundle = await profile(folder, args)
    guidance = args.bundle.resolve().parent / 'context/work-execution.md'
    guidance_text = guidance.read_text() if guidance.is_file() else ''
    document = yaml.safe_load(bundle.read_text().split('---')[1])
    document['session']['orchestrator']['config']['max_iterations'] = 5
    bundle.write_text('---\n' + yaml.safe_dump(document, sort_keys=False) + '---\n')
    files = {}
    for name, label, value in zip(FILES, EXPECTED['ids'], (11, 17, 23)):
        content = json.dumps({'id': label, 'value': value,
                             'details': 'Supplementary fixture detail not needed for the requested aggregate. ' * 80}) + '\n'
        (folder / 'workspace' / name).write_text(content)
        files[name] = {'sha256': hashlib.sha256(content.encode()).hexdigest(), 'bytes': len(content.encode())}
    report = {'schemaVersion': 1, 'providerInstance': args.provider, 'model': provider['config'].get('default_model'),
              'effort': provider['config'].get('reasoning_effort'), 'installed': installed_revisions(), 'sources':source_inventory(args),
              'evidence': 'real configured provider, full Work include graph, reviewed local tool/loop overrides, isolated Unified workers',
              'guidance': {'sha256': hashlib.sha256(guidance_text.encode()).hexdigest(), 'legacyExitCodeClaim': 'Bash returns an object containing `stdout`, `stderr` and `exit_code`' in guidance_text},
              'fixture': files, 'expected': EXPECTED, 'arms': [], 'order': ['direct', 'programmatic'], 'passed': False,
              'limits': 'One input per arm, max five loop iterations; no reruns. Serial second arm may benefit from provider cache. Full observed call usage includes system prompts, schemas and any extra discovery. Catalog JSON bytes are inventory bytes, not exact provider wire bytes. Provider internal retries may not be separately reported. Wall time starts at input submission; startup/catalog time is also reported. No universal speedup or savings claim.'}
    app = await create_app(folder/'app', workspace=folder/'workspace', voice=False, background_updates=False, preload_providers=False)
    service = app['service']; service.runtime.command = [sys.executable, str(Path(worker.__file__).resolve())]
    observation = Observation(); original = service.on_runtime_event
    async def event(kind, data):
        observation.add(kind, data); await original(kind, data)
    service.on_runtime_event = event
    runner = web.AppRunner(app); await runner.setup(); site = web.TCPSite(runner, '127.0.0.1', 0); await site.start()
    url = f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}'
    try:
        async with SessionClient(url, app['control_token'], 'tool-benchmark') as client:
            for mode in report['order']:
                arm = {'mode': mode, 'passed': False}; report['arms'].append(arm)
                started = time.monotonic(); sid = None
                try:
                    created = await client.create_session({'title':'Synthetic '+mode+' tool comparison', 'bundle':str(bundle), 'workspace':str(folder/'workspace')}, command_id='create-'+mode)
                    sid = created['state']['selectedSessionId']; arm['sessionId'] = sid
                    await service.runtime.start(service._session(sid), service.on_runtime_event)
                    catalog = await service.runtime.control(sid, 'catalog.inspect')
                    tools = catalog['tools']; serialized = json.dumps(tools, sort_keys=True).encode()
                    arm['catalog'] = {'count':len(tools), 'jsonBytes':len(serialized), 'sha256':hashlib.sha256(serialized).hexdigest(), 'passiveHarnessReads':1}
                    private_json(folder/(mode+'-catalog.json'), tools)
                    sent = time.monotonic(); arm['startupAndCatalogSeconds'] = round(sent-started, 4)
                    print(json.dumps({'phase':'arm-ready', 'mode':mode}), flush=True)
                    await client.command(sid, 'conversation.send', {'text':COMMON+MODES[mode]}, command_id='run-'+mode)
                    await observation.wait(lambda: service._session(sid)['status'] == 'idle' and any(
                        row.get('kind') == 'assistant.message' and row.get('sessionId') == sid for row in observation.events), 240)
                    arm['elapsedInputToResultSeconds'] = round(time.monotonic()-sent, 4)
                    arm['elapsedIncludingStartupSeconds'] = round(time.monotonic()-started, 4)
                    history = await service.runtime.control(sid, 'history.snapshot'); private_json(folder/(mode+'-history.json'), history)
                    final = '\n'.join(row.get('text','') for row in observation.events if row.get('kind') == 'assistant.message' and row.get('sessionId') == sid)
                    arm.update(summarize(mode, history, final)); arm['finalResponse'] = final
                    execution = service._session(sid).get('execution', {})
                    arm['observedToolCalls'] = [{key:node[key] for key in ('id','label','phase','input','parentId') if key in node}
                                               for node in execution.get('nodes',[]) if node.get('kind') == 'tool']
                    arm['usage'] = usage_snapshot(service._session(sid))
                    arm['nativeUsage'] = await service.runtime.control(sid, 'usage.inspect')
                    arm['inclusiveMetricsKnown'] = all(arm['usage']['metrics'][key]['status'] == 'known' for key in ('grossInputTokens','grossTotalTokens','costUsd'))
                    actual_commands = []
                    for node in arm['observedToolCalls']:
                        if node.get('label') == 'bash':
                            try: actual_commands.append(json.loads(node.get('input','{}')).get('command'))
                            except ValueError: actual_commands.append(None)
                    arm['exactUnderlyingReads'] = sorted(str(command) for command in actual_commands) == sorted('cat '+name for name in FILES)
                    arm['passed'] = all(arm[k] for k in ('correctOutput','matchesRequestedMode','successfulToolResults','inclusiveMetricsKnown','exactUnderlyingReads'))
                    print(json.dumps({'phase':'arm-finished','mode':mode,'passed':arm['passed'],'modelCalls':arm['usage']['calls'],'seconds':arm['elapsedInputToResultSeconds']}), flush=True)
                except Exception as exc:
                    arm['errorType'] = type(exc).__name__
                    (folder/(mode+'-error.txt')).write_text(str(exc))
                    if sid: arm['usage'] = usage_snapshot(service._session(sid))
                finally:
                    if sid: await service.runtime.stop(sid)
                private_json(folder/'report.json', report)
            report['sameCatalog'] = len(report['arms']) == 2 and report['arms'][0].get('catalog') == report['arms'][1].get('catalog')
            report['fixturesUnchanged'] = all(hashlib.sha256((folder/'workspace'/name).read_bytes()).hexdigest() == item['sha256'] for name,item in files.items())
            report['passed'] = all(a['passed'] for a in report['arms']) and report['sameCatalog'] and report['fixturesUnchanged']
    finally:
        await runner.cleanup()
        private_json(folder/'events.json', observation.events)
        private_json(folder/'report.json', report)
        report['credentialCleanup'] = clean_credentials(folder, provider)
        private_json(folder/'report.json', report)
    print(json.dumps({'passed':report['passed'], 'report':str(folder/'report.json'), 'cleanup':report.get('credentialCleanup')}), flush=True)
    return report['passed']


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--allow-live', action='store_true'); parser.add_argument('--provider', required=True)
    parser.add_argument('--settings', type=Path, default=Path.home()/'.amplifier/settings.yaml')
    parser.add_argument('--bundle', type=Path, required=True); parser.add_argument('--module-root', type=Path, required=True)
    parser.add_argument('--module-source', action='append', default=[]); parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not args.allow_live: parser.error('--allow-live is required for paid provider calls')
    raise SystemExit(0 if asyncio.run(run(args)) else 1)


if __name__ == '__main__': main()
