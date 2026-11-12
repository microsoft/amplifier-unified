"""Synthetic repositories for standalone Git mechanism checks."""
from amplifier_worktrees.git import git


def repository(tmp_path):
    root = tmp_path / 'repo'; root.mkdir()
    git(root, 'init', '-b', 'main')
    git(root, 'config', 'user.name', 'Fixture'); git(root, 'config', 'user.email', 'fixture@example.invalid')
    (root / 'a.txt').write_text('original\n')
    (root / '.gitignore').write_text('ignored.txt\n')
    git(root, 'add', '.'); git(root, 'commit', '-m', 'base')
    return root



def create(manager, source, **kwargs):
    return manager.create(source, command_id=kwargs.pop('command_id', 'create'), expected_revision=manager.inspect(source)['sourceRevision'], **kwargs)
