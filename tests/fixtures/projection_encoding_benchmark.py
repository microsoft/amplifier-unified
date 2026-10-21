"""Manual owned-DTU microbenchmark; no model, service, network, or live data.

Pass an unchanged history_projection_process.py from the comparison revision.
Both implementations receive the same frozen 5,272-workspace input. Timing is
encoding only; equivalence and reservation checks happen outside each interval.
"""
import argparse
import hashlib
import importlib.util
import json
import platform
from pathlib import Path
import statistics
import sys
import tempfile
import time

from amplifier_web import history_projection_process as candidate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--before-source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--pairs', type=int, default=15)
    args = parser.parse_args()
    if not 3 <= args.pairs <= 100:
        parser.error('--pairs must be between 3 and 100')
    args.output.mkdir(parents=True, exist_ok=True)
    spec = importlib.util.spec_from_file_location(
        'amplifier_web._encoding_before', args.before_source)
    baseline = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(baseline)
    inputs = {
        'workspaces': [{'id': f'workspace-{i:05d}',
            'path': f'/synthetic/projects/project-{i:05d}',
            'name': f'Project {i:05d} — 雪 🔧',
            'available': i % 17 != 0, 'archived': i % 31 == 0}
            for i in range(5272)],
        'view': {'type': 'workspaces', 'filter': '', 'offset': 0, 'limit': 50},
    }
    frozen = json.dumps(inputs, ensure_ascii=False, allow_nan=False,
                        sort_keys=True, separators=(',', ':')).encode('utf-8')
    digest = hashlib.sha256(frozen).hexdigest()
    (args.output / 'frozen-input.json').write_bytes(frozen)
    samples = {'before': [], 'after': []}
    with tempfile.TemporaryDirectory(prefix='encoding-benchmark-', dir=args.output) as home:
        root = Path(home)
        owners = {'before': baseline.ProcessProjector(root),
                  'after': candidate.ProcessProjector(root)}
        for index in range(args.pairs + 2):
            order = ('before', 'after') if index % 2 == 0 else ('after', 'before')
            for name in order:
                owner = owners[name]
                path = root / f'{name}.input'
                assert owner._input_bytes == 0
                cpu_start, wall_start = time.process_time_ns(), time.perf_counter_ns()
                size, actual_digest = owner._encode(path, inputs)
                wall_ns = time.perf_counter_ns() - wall_start
                cpu_ns = time.process_time_ns() - cpu_start
                assert path.read_bytes() == frozen
                assert size == owner._input_bytes == len(frozen)
                assert actual_digest == digest
                path.unlink()
                owner._release(size)
                assert owner._input_bytes == 0
                if index >= 2:
                    samples[name].append({'wallMs': wall_ns / 1e6, 'cpuMs': cpu_ns / 1e6})
    medians = {name: {metric: statistics.median(s[metric] for s in values)
                     for metric in ('wallMs', 'cpuMs')} for name, values in samples.items()}
    result = {
        'schemaVersion': 1, 'workspaceCount': 5272,
        'inputBytes': len(frozen), 'inputSha256': digest,
        'beforeSourceSha256': hashlib.sha256(args.before_source.read_bytes()).hexdigest(),
        'afterSourceSha256': hashlib.sha256(Path(candidate.__file__).read_bytes()).hexdigest(),
        'python': sys.version, 'platform': platform.platform(),
        'pairs': args.pairs, 'warmupPairs': 2, 'order': 'alternating',
        'allSamplesExactBytesDigestAndChargedBudget': True,
        'medians': medians, 'samples': samples,
        'afterToBeforeWallRatio': medians['after']['wallMs'] / medians['before']['wallMs'],
        'scope': 'Single-thread synthetic encoding only; not end-to-end navigation or event-loop latency.',
    }
    (args.output / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({key: value for key, value in result.items() if key != 'samples'}, indent=2))


if __name__ == '__main__':
    main()
