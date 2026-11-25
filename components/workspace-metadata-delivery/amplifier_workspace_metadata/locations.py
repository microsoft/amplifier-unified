"""Read-only interpretation of a legacy registration's local location."""
from typing import NamedTuple


class LocalLocation(NamedTuple):
    """A snapshot in the caller's existing installation/account scope.

    The workspace ID is unchanged; this is not a new location identity, history
    locator or permission grant. Availability is an observation, not a probe.
    """

    workspace_id: str
    path: str
    available: bool | None


def local_location(registration) -> LocalLocation | None:
    """Interpret the existing path without modifying or resolving the row.

    The registration remains the sole writable owner. Missing, None or empty
    paths have no known local location; an unavailable path is still known.
    Call again to observe later changes. Custom fields remain opaque.
    """
    path = registration.get("path")
    if not path:
        return None
    return LocalLocation(registration["id"], path, registration.get("available"))
