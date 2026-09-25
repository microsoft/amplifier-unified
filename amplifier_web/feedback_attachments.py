"""Private local staging and documented GitHub Git Data attachment transport.

GitHub's issue API accepts Markdown, not file uploads. Explicit submissions use
an isolated root commit and branch containing only their reviewed files. Links
use that immutable commit; no main-branch writes, release assets, or public URLs
with credentials are involved. A failed/lost write is never replayed here.
"""
from __future__ import annotations

import base64
import hashlib
import html
import os
import re
import stat
from urllib.parse import quote

from . import attachments

MAX_FILES = 8
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 24 * 1024 * 1024


class BeforeUploadError(ValueError):
    """A read-only preflight refused a destination before any file was sent."""


def read_verified(home, row):
    """Read exactly the staged bytes, refusing changes and symbolic links."""
    directory = attachments.location(home, row["id"])
    # Open each component relative to a held descriptor. O_NOFOLLOW on only
    # the final file would leave a directory-swap race between validation/read.
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    root_fd = os.open(directory.parent, flags)
    try:
        directory_fd = os.open(row["id"], flags, dir_fd=root_fd)
        try:
            with os.fdopen(os.open("content", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd), "rb") as handle:
                info = os.fstat(handle.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size != row["size"]:
                    raise ValueError("A feedback attachment changed. Remove it and attach it again.")
                data = handle.read(MAX_FILE_BYTES + 1)
        finally:
            os.close(directory_fd)
    finally:
        os.close(root_fd)
    if len(data) != row["size"] or hashlib.sha256(data).hexdigest() != row["sha256"]:
        raise ValueError("A feedback attachment changed. Remove it and attach it again.")
    return data


def object_sha(result):
    value = result.get("sha", "")
    if not isinstance(value, str) or not re.fullmatch(r"(?:[a-f0-9]{40}|[a-f0-9]{64})", value):
        raise ValueError("GitHub did not return a valid attachment receipt")
    return value


async def upload(repository, identity, files, github_api):
    """Upload already verified, bounded bytes, once per accepted submission."""
    prefix = "repos/" + repository
    # Refuse to upload attachments if this intended private destination changes.
    target = await github_api(prefix, None)
    visibility = 'private' if target.get('private') is True else 'public' if target.get('private') is False else 'unknown'
    for row, _ in files:
        excerpt = row.get('excerpt')
        if excerpt:
            if (excerpt['repository'] != repository or excerpt['visibility'] != visibility
                    or target.get('full_name', '').casefold() != repository.casefold()
                    or row['sha256'] != excerpt['sha256']):
                raise BeforeUploadError('The excerpt destination or visibility changed. Review it again. Nothing was sent.')
        elif visibility != 'private':
            raise BeforeUploadError('The attachment destination is no longer private. Nothing was sent.')
    tree = []
    for row, data in files:
        blob = object_sha(await github_api(prefix + "/git/blobs", {
            "encoding": "base64", "content": base64.b64encode(data).decode("ascii"),
        }))
        tree.append({"path": row["id"] + "/" + row["name"], "mode": "100644", "type": "blob", "sha": blob})
    tree_sha = object_sha(await github_api(prefix + "/git/trees", {"tree": tree}))
    commit = object_sha(await github_api(prefix + "/git/commits", {
        "message": "Feedback attachments " + identity, "tree": tree_sha, "parents": [],
    }))
    reference = await github_api(prefix + "/git/refs", {"ref": "refs/heads/feedback-assets/" + identity, "sha": commit})
    if object_sha(reference.get("object", {})) != commit:
        raise ValueError("GitHub did not confirm attachment storage")
    return [{**{key: row[key] for key in ("id", "name", "mime", "size")},
             "visibility": visibility, "url": "https://github.com/" + repository + "/blob/" + commit + "/" + quote(item["path"], safe="/")}
            for (row, _), item in zip(files, tree)]


def markdown(rows):
    # GitHub's authenticated file view is a dependable fallback for private
    # images too; don't claim its image proxy can display private raw assets.
    def label(value):
        return re.sub(r"([\\`*_[\]{}()!|])", r"\\\1", html.escape(value))
    return "\n\n### Attachments\n\n" + "\n".join(
        "- [" + label(row["name"]) + "](" + row["url"] + ") — " + str(row["size"]) + " bytes"
        for row in rows
    ) + ("\n\nFiles are publicly visible and retained in repository history." if any(row.get('visibility') == 'public' for row in rows) else "\n\nFiles are retained in this private repository; repository access is required.")
