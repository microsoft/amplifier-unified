"""Reconcile discovered workspace metadata in caller-owned registrations."""
from __future__ import annotations

import copy
from typing import NamedTuple
import uuid


class RegistrationReconciliation(NamedTuple):
    by_id: dict
    hidden_ids: set
    registrations_changed: bool
    hidden_changed: bool
    remaps: list[tuple[str, str, str | None]]


def reconcile(registrations, known, observed, *, hidden_ids, managed_paths):
    """Update registration rows in place and report ordered reference remaps.

    The caller owns both mutable lists, applies each remap in order, and persists
    the affected state under its existing lock. Observations are not modified.
    A remap carries the observed destination path at that point in the scan.
    """
    hidden_workspaces = set(hidden_ids)
    registrations_changed = hidden_changed = False
    remaps = []
    workspaces = {row['id']: row for row in registrations}
    # Most refreshes contain no unresolved folders. Index the
    # exceptions once instead of rescanning every registration
    # for every discovered workspace while holding the app lock.
    unresolved_by_project = {}
    for item in registrations:
        if not item.get('path'):
            unresolved_by_project.setdefault(item.get('nativeProject'), {})[item['id']] = item
    for row in known:
        previous = workspaces.get(row['id'])
        if previous and previous.get('path') == row.get('path') and previous.get('available') != row['available']:
            previous['available'] = row['available']
            registrations_changed = True
    for row in observed:
        if row.get('path') in managed_paths:
            continue
        unresolved_id = (uuid.uuid5(uuid.NAMESPACE_URL, f"amplifier-project:{row['nativeProject']}").hex
                         if hidden_workspaces and row.get('path') else None)
        if unresolved_id is not None and unresolved_id in hidden_workspaces and row['id'] not in hidden_workspaces:
            hidden_workspaces.add(row['id'])
            hidden_ids.append(row['id'])
            hidden_changed = True
        if row['id'] in hidden_workspaces:
            continue
        previous = workspaces.get(row['id'])
        previous_project = previous.get('nativeProject') if previous else None
        unresolved = [item for identity, item in unresolved_by_project.get(row['nativeProject'], {}).items()
                      if identity != row['id']]
        if previous is None:
            registrations.append(dict(row)); workspaces[row['id']] = registrations[-1]
            previous = workspaces[row['id']]
            registrations_changed = True
        else:
            for key in ('nativeProject', 'available', 'sessionCount', 'workerSessionCount'):
                if key in row and previous.get(key) != row[key]:
                    previous[key] = row[key]; registrations_changed = True
        if not previous.get('path'):
            # Later rows in this same scan must see additions
            # and identity changes, just as a fresh list scan did.
            project = previous.get('nativeProject')
            if previous_project != project:
                unresolved_by_project.get(previous_project, {}).pop(previous['id'], None)
            unresolved_by_project.setdefault(project, {})[previous['id']] = previous
        for old in unresolved:
            # Migrate the generated placeholder, preserving any
            # user customization and every reference to it.
            if old.get('name') != old.get('nativeProject'):
                if previous.get('name') == row.get('name'):
                    previous['name'] = old['name']
                elif old.get('name') != previous.get('name'):
                    aliases = previous.setdefault('aliases', [])
                    if old['name'] not in aliases:
                        aliases.append(old['name'])
            for key, value in old.items():
                if key not in {'id', 'name', 'nativeProject', 'path', 'available', 'sessionCount', 'workerSessionCount'}:
                    previous.setdefault(key, copy.deepcopy(value))
            remaps.append((old['id'], row['id'], row.get('path')))
            registrations.remove(old)
            workspaces.pop(old['id'], None)
            unresolved_by_project.get(old.get('nativeProject'), {}).pop(old['id'], None)
            registrations_changed = True
    return RegistrationReconciliation(
        workspaces, hidden_workspaces, registrations_changed, hidden_changed, remaps)
