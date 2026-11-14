"""Concrete diagnostic image binding; unknown original policy always refuses."""
from __future__ import annotations


class UnknownHistoricalMedia(ValueError):
    pass


def capture_owner(app, row, operation):
    # Both actual historical owners were inspected: old UUID-upload metadata has
    # no creation/expiry/admission receipt; legacy computer issuance was memory
    # only. There is no durable capture-record-to-original-observation binding.
    # Modern accepted refs already use their owning readers and need no inline
    # conversion. An event timestamp, path or equal digest cannot recreate the
    # missing authority. Keep this explicit rather than discovering a fictitious
    # callback or silently assigning durable/null expiry to historical pixels.
    raise UnknownHistoricalMedia('Original image observation and expiry receipt is unavailable; historical pixels were kept unchanged')