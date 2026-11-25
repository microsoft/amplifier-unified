"""Serve an upload UUID from its original file or its private, committed binding."""
from contextlib import ExitStack, closing
import json
import os
import stat

from aiohttp import web

from . import attachments
from .session_files import amplifier_home, validate_id


def _grant(directory):
    # Pin the UUID directory during the read. A symlink, shared metadata file or
    # an existing (even broken) content entry cannot authorize the alias path.
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY
    with ExitStack() as stack:
        root = os.open(directory.parent, flags)
        stack.callback(os.close, root)
        slot = os.open(directory.name, flags, dir_fd=root)
        stack.callback(os.close, slot)
        try:
            os.stat("content", dir_fd=slot, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise ValueError("Original attachment entry is still present")
        fd = os.open("metadata.json", os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=slot)
        stream = stack.enter_context(os.fdopen(fd, "rb"))
        for info in (os.fstat(root), os.fstat(slot), os.fstat(fd)):
            if info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise ValueError("Attachment grant is not private")
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 4096:
            raise ValueError("Invalid attachment grant")
        data = stream.read(4097)
        after = os.fstat(fd)
        if len(data) != info.st_size or (info.st_mtime_ns, info.st_ctime_ns) != (after.st_mtime_ns, after.st_ctime_ns):
            raise ValueError("Attachment metadata changed")
        row = json.loads(data)
        public = attachments.public_metadata(row, directory.name)
        return public, row.get("sessionMedia"), info.st_mtime_ns


class _OpenSnapshot:
    """aiohttp opens a second reader on the verified file description."""
    def __init__(self, stream):
        self.stream = stream

    def open(self, mode):
        return os.fdopen(os.dup(self.stream.fileno()), mode)


class _SnapshotResponse(web.FileResponse):
    """Use the same aiohttp range, conditional and HEAD implementation as originals."""
    def __init__(self, open_snapshot, modified):
        super().__init__("attachment")
        self._open_snapshot = open_snapshot
        self._modified = modified

    def _make_response(self, request, accept_encoding):
        # Defer opening until aiohttp's file executor owns the entire operation.
        # Cancellation before prepare leaves no open observer or snapshot.
        try:
            with self._open_snapshot() as stream:
                os.utime(stream.fileno(), ns=(self._modified, self._modified))
                self._source = _OpenSnapshot(stream)
                try:
                    return super()._make_response(request, "")
                finally:
                    del self._source
        except (ValueError, OSError, KeyError, TypeError):
            self.headers.pop("Content-Disposition", None)
            self.headers.pop("Content-Type", None)
            raise FileNotFoundError("Attachment not found") from None

    def _get_file_path_stat_encoding(self, accept_encoding):
        # Never reopen a changing asset by pathname (including /dev/fd).
        return self._source, os.fstat(self._source.stream.fileno()), None


def download(home, identity):
    directory = attachments.location(home, identity)
    if os.name != "posix" or (directory / "content").exists():
        path, row = attachments.file_path(home, identity)
        return web.FileResponse(path), row

    row, binding, modified = _grant(directory)
    if not isinstance(binding, dict) or set(binding) != {"project", "ref"}:
        raise ValueError("No saved attachment binding")
    project, ref = binding["project"], binding["ref"]
    if (not isinstance(project, str) or project in {"", ".", ".."} or "/" in project
            or "\\" in project or "\0" in project or not isinstance(ref, dict)):
        raise ValueError("Invalid saved attachment location")
    sid = validate_id(ref.get("session_id"))
    origin = ref.get("provenance")
    if (not isinstance(origin, dict) or origin.get("kind") != "upload" or origin.get("id") != identity
            or origin.get("session_id") != sid or origin.get("expires_at") is not None
            or row["mime"] not in attachments._IMAGE_MIMES or ref.get("mime") != row["mime"]
            or ref.get("bytes") != row["size"]):
        raise ValueError("Saved media does not represent this upload")

    try:
        from amplifier_foundation.session.media import SessionMediaStore
    except ImportError:
        raise ValueError("This Foundation version does not support session media") from None
    if not callable(getattr(SessionMediaStore, "resolve_checkpointed_ref_for_source_read", None)):
        raise ValueError("This Foundation version cannot open a checkpointed download")

    def open_snapshot():
        def authorize(candidate, *, purpose):
            return (purpose == "download" and candidate == ref
                    and _grant(directory) == (row, binding, modified)
                    and observer.get_origin_classification(candidate) == "composer-upload")

        root = amplifier_home() / "projects" / project / "sessions" / sid / "media"
        with closing(SessionMediaStore(root, sid, read_only=True, source_read_verifier=authorize)) as observer:
            return observer.resolve_checkpointed_ref_for_source_read(ref)

    return _SnapshotResponse(open_snapshot, modified), row
