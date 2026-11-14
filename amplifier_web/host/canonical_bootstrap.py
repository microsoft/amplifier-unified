"""Normal-caller selection of Foundation's prospective Linux source owner.

Call ``SourceBootstrap.acquire`` before any store, capture hook or native
transcript guard can create the selected root. This is not migration or proof of
quiescence for old writers. An occupied legacy root remains legacy; a versioned
root must pass Foundation's genuine source and enrollment checks, without a
catalog/JSON enable flag or automatic conversion.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import os
from pathlib import Path
import stat
import sys
from typing import TYPE_CHECKING
from uuid import uuid4

from ..session_files import sessions_dir, validate_id

if TYPE_CHECKING:
    from amplifier_foundation.session.transcript_lifetime import VersionedSessionBinding


def _present(path: Path) -> bool:
    # Unlike exists(), this also recognizes dangling links as occupied/refused.
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    return True


def _prepare_parent(path: Path) -> None:
    """Create only missing private components; never chmod an existing path.

    Walk without following links, including while creating missing ancestors.
    Foundation then pins and validates the actual sessions parent itself.
    """
    descriptor = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.parts[1:]:
            try:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                dir_fd=descriptor)
            except FileNotFoundError:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=descriptor)
                except FileExistsError:
                    pass  # A competitor is checked by the no-follow open below.
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
    finally:
        os.close(descriptor)


def _acquire(directory: Path, session_id: str, *, resumed: bool,
             admit: Callable[[], None]) -> VersionedSessionBinding:
    from amplifier_foundation.session.transcript_lifetime import VersionedSessionBinding

    admit()
    if resumed:
        from amplifier_foundation.session.transcript_source import open_committed_source
        # Read-only, bounded-memory verification, never a native writable FD.
        # The lease acquisition below revalidates this exact prefix: a race is
        # refusal, not an invitation to reopen/retry a moving source.
        with open_committed_source(directory) as source:
            expected = source.commit
        admit()
        binding = VersionedSessionBinding.resume(directory, session_id=session_id,
                                                  expected=expected)
    else:
        binding = VersionedSessionBinding.create_new(
            directory, session_id=session_id, operation_id='bootstrap-' + uuid4().hex,
            admit=admit)
    try:
        admit()
        binding.check()
    except BaseException as error:
        binding.close()
        error.add_note(f'Bootstrap custody was released; inspect the retained source {directory}. '
                       'No acquisition or publication was replayed.')
        raise
    return binding


@dataclass(frozen=True)
class _ChildFactory:
    parent: Path
    root: VersionedSessionBinding
    admit: Callable[[], None]

    def __call__(self, directory, *, session_id: str, resumed: bool) -> VersionedSessionBinding:
        validate_id(session_id)
        selected = Path(directory)
        if (not selected.is_absolute() or '..' in selected.parts
                or selected != self.parent / session_id):
            raise ValueError('The child source must match this workspace and session ID.')
        if type(resumed) is not bool:
            raise TypeError('Explicit child new-versus-resume intent is required.')

        def admission():
            self.root.check()
            self.admit()
            self.root.check()

        # New means absent only; resume means enrolled only. Neither path may
        # silently downgrade to legacy or adopt an existing nonenrolled root.
        return _acquire(selected, session_id, resumed=resumed, admit=admission)


@dataclass(frozen=True)
class SourceBootstrap:
    """Custody handed to the Worker, which must close it after physical cleanup.

    ``binding is None`` means keep the existing legacy/platform path, with no
    child factory. ``resumed`` is explicit (an initialized-empty new root is
    False). Pass all three fields to the caller's existing binding seams.
    ``admit`` must be the live application shutdown/Stop guard, raising on
    refusal, not a saved permission flag. It is rechecked during initialization
    and before handing out either root or child custody.
    """
    binding: VersionedSessionBinding | None
    resumed: bool
    child_factory: _ChildFactory | None

    @classmethod
    def acquire(cls, workspace, session_id: str, *, admit: Callable[[], None]) -> SourceBootstrap:
        if not callable(admit):
            raise TypeError('A live application shutdown/Stop admission guard is required.')
        validate_id(session_id)
        admit()
        parent = sessions_dir(workspace).absolute()
        directory = parent / session_id
        occupied = _present(directory)
        if occupied and not stat.S_ISDIR(directory.lstat().st_mode):
            raise ValueError('The selected session root is not a real directory.')
        versioned = occupied and _present(directory / 'transcript.v2')
        if sys.platform != 'linux':
            if versioned:
                raise ValueError('Prospective versioned custody is qualified on Linux only.')
            return cls(None, False, None)
        if occupied and not versioned:
            # Probe only: even an empty existing root may belong to an older
            # writer. Do not create files, inspect enrollment JSON or convert it.
            return cls(None, False, None)
        if not occupied:
            admit()
            _prepare_parent(parent)
        binding = _acquire(directory, session_id, resumed=occupied, admit=admit)
        return cls(binding, occupied, _ChildFactory(parent, binding, admit))
