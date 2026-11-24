"""Borrow one ordinary host projection while a chat's previous summary finishes."""
import asyncio
import copy
from contextlib import asynccontextmanager
from contextvars import ContextVar

_ACTIVE = ContextVar("unified_foreground_preparation", default=None)


def active():
    candidate = _ACTIVE.get()
    if candidate is not None and candidate.task is asyncio.current_task():
        return candidate
    return None


class _Candidate:
    def __init__(self, surface, selection, original, request, options):
        self.task = asyncio.current_task()
        self.surface, self.selection = surface, selection
        self.original, self.request = original, request
        self.options = copy.deepcopy(options)
        self.entry = self.encoded = None
        self.work_owner = getattr(getattr(surface.delivery, "discard_work", None), "__self__", None)
        self.work = None
        self.consumed = False

    def check_selection(self, execution, kwargs):
        updates, options = self.selection._selection_overrides(self.original, dict(kwargs))
        row = self.selection._cached_request(self.original, updates)
        if (self.consumed or execution is not self.surface or row is None
                or row.selected is not self.request or options != self.options):
            raise ValueError("Selected provider or options changed after ordinary request preparation")

    def check_surface(self, entry, options):
        if self.consumed or entry is None or entry is not self.entry or self.options != options:
            raise ValueError("Ordinary request preparation expired or changed")

    def consume(self):
        if self.work is not None:
            self.work_owner._remember_work(*self.work)
            self.work = None
        self.consumed = True


@asynccontextmanager
async def prepare(surface, original, options, *, selection=None):
    """The usual complete(original) consumes this view. Exiting unused abandons it."""
    from .surface_delivery import SurfaceProvider
    if not isinstance(surface, SurfaceProvider) or active() is not None:
        yield None
        return
    options = dict(options or {})
    request = original
    if selection is not None:
        if (len(selection._selected_requests) >= 4 or
                any(row.original is original for row in selection._selected_requests)):
            yield None
            return
        request, options = selection._selected_request(original, options)
    candidate = _Candidate(surface, selection, original, request, options)
    token = _ACTIVE.set(candidate)
    try:
        # Leave existing budget/refit candidates entirely in their owners' hands.
        if len(surface.prepared) >= 4 or any(row[0] is request for row in surface.prepared):
            yield None
            return
        view = await surface._prepare(request, request_options=options)
        candidate.entry = next(row for row in surface.prepared if row[0] is request)
        candidate.encoded = candidate.entry[2] or view.model_dump_json()
        yield view
    finally:
        _ACTIVE.reset(token)
        candidate.task = None
        if candidate.entry is not None and any(row is candidate.entry for row in surface.prepared):
            surface._release_media(request)
            surface.prepared = [row for row in surface.prepared if row is not candidate.entry]
        if selection is not None and any(row.selected is request for row in selection._selected_requests):
            selection._forget_request(original)
        candidate.work = None
