"""Disposable bounded Work-window process, sharing the existing transport."""
import argparse
import math
import time
from .history_projection_worker import run, MEMORY_LIMIT


class WindowModel:
    def __init__(self, cache_home):
        # The transport's host-owned home, never a client-supplied database path.
        self.cache_home = cache_home

    def project(self, inputs, *, check_budget):
        from .event_window import read_window, read_field, read_proof_step, StaleWindow
        try:
            if inputs.get('operation') == 'proof-step':
                deadline = inputs.get('deadline')
                if type(deadline) not in (int, float) or not math.isfinite(deadline):
                    raise ValueError('A finite absolute proof deadline is required')
                deadline = min(deadline, time.monotonic() + 20)
                def guard():
                    check_budget()
                    if time.monotonic() >= deadline:
                        raise TimeoutError('The cumulative root proof deadline expired')
                try:
                    guard()
                    page = read_proof_step(inputs['session'], window_key=inputs['windowKey'],
                        cursor_key=bytes.fromhex(inputs['cursorKey']), before=inputs.get('before'),
                        limit=inputs.get('limit', 100), check_budget=guard, cache_home=self.cache_home,
                        expected_source=inputs.get('expectedSource'), expected_index=inputs.get('expectedIndex'))
                    guard()
                except TimeoutError:
                    return {'status': 'retry', 'reason': 'timeout'}
                return {'status': 'complete', 'page': page}
            if inputs.get('operation') == 'window':
                page = read_window(inputs['session'], window_key=inputs['windowKey'],
                    cursor_key=bytes.fromhex(inputs['cursorKey']), before=inputs.get('before'),
                    check_budget=check_budget, cache_home=self.cache_home)
                return {'status': 'complete', 'page': page}
            if inputs.get('operation') == 'field':
                value = read_field(inputs['session'], inputs['reference'],
                    offset=inputs.get('offset', 0), limit=inputs.get('limit', 4096),
                    check_budget=check_budget)
                return {'status': 'complete', 'field': value}
            raise ValueError('Unknown Work window operation')
        except StaleWindow:
            return {'status': 'retry', 'reason': 'source_changed'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--spool', required=True)
    parser.add_argument('--cache-home', required=True)
    parser.add_argument('--memory-limit', type=int, default=MEMORY_LIMIT)
    args = parser.parse_args()
    run(args.spool, args.cache_home, model_factory=WindowModel,
        memory_limit=args.memory_limit, input_limit=64 * 1024, output_limit=4 * 1024 * 1024)
