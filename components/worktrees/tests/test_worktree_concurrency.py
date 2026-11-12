import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from amplifier_worktrees import GitWorktrees
from worktree_fixtures import repository


@pytest.mark.parametrize('action', ['create', 'attach'])
def test_command_identity_cannot_race_across_different_repository_locks(tmp_path, action):
    roots = []
    for name in ('one', 'two'):
        parent = tmp_path / name; parent.mkdir()
        roots.append(repository(parent))
    store = tmp_path / 'managed'
    revisions = [GitWorktrees(store).inspect(root)['sourceRevision'] for root in roots]
    barrier = threading.Barrier(2)
    def compete(index):
        manager = GitWorktrees(store)
        barrier.wait(timeout=5)
        try:
            if action == 'create':
                return manager.create(roots[index], command_id='same-command', expected_revision=revisions[index], session_id=str(index))
            return manager.attach(roots[index], source=roots[index], command_id='same-command', session_id=str(index))
        except ValueError as exc:
            return str(exc)
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(compete, range(2)))
    accepted = [row for row in outcomes if isinstance(row, dict)]
    refused = [row for row in outcomes if isinstance(row, str)]
    assert len(accepted) == len(refused) == 1
    assert 'command ID' in refused[0]
    assert GitWorktrees(store).records() == accepted
    assert accepted[0]['status'] == 'ready'
