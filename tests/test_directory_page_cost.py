"""An ordinal page needs ordered selection, not a rank for every owner."""
import json
import time

from amplifier_web.directory_state import _ranked
from test_history_directory_exact_ids import native_fixture


def test_small_pages_keep_global_positions_without_full_window_ranking(tmp_path):
    state, directory, _, _, _ = native_fixture(tmp_path, 27000, 6)
    sql, params = directory.effective(state, fields=('id', 'ordinal'))
    expected = [tuple(row) for row in directory.db.execute(
        sql + ' SELECT id,ordinal FROM directory_rows ORDER BY ordinal,id', params)]
    measurements = []
    for offset in (0, 13500, len(expected) - 5):
        steps = 0
        def progress():
            nonlocal steps
            steps += 1000
            return 0
        directory.db.set_progress_handler(progress, 1000)
        started = time.thread_time_ns()
        try:
            rows = _ranked(directory, limit=5, offset=offset)
        finally:
            directory.db.set_progress_handler(None, 0)
        measurements.append({'offset': offset, 'vm': steps,
                             'cpuMs': (time.thread_time_ns() - started) / 1e6})
        assert [position for position, _ in rows] == list(range(offset, offset + 5))
        assert [row['id'] for _, row in rows] == [row[0] for row in expected[offset:offset + 5]]
    print(json.dumps({'directoryPageCost': measurements}))
    # 27k native owners plus six local owners, with five returned bodies.
    # The old window-rank query used 2.38–2.49M instructions per page; direct
    # ordered selection uses 1.30–1.36M, including the exact metadata lookup.
    assert max(item['vm'] for item in measurements) < 1_600_000
    assert len(state['sessions']) == 6
