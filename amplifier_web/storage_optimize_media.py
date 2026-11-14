"""Concrete diagnostic image binding; unknown original policy always refuses."""
from __future__ import annotations


class UnknownHistoricalMedia(ValueError):
    pass


def capture_owner(app, row, operation):
    # The actual original-receipt adapter is owned by upload/capture producers.
    # No provenance can be reconstructed from pixels or a provider-wire copy.
    try:
        from .media_assets import historical_capture_owner
    except ImportError:
        raise UnknownHistoricalMedia('Historical image origin receipts are not qualified on this installation') from None
    return historical_capture_owner(app, row, operation)