"""Opt-in wire deduplication; component snapshots keep their existing contract."""

def compact(result):
    snapshots = result.get('snapshots', {})
    shared, refs, groups = [], {}, {}
    for identity, snapshot in snapshots.items():
        for key, value in snapshot.items():
            if isinstance(value, (dict, list)) and value:
                groups.setdefault(key, []).append((identity, value))
    copies = {identity: dict(snapshot) for identity, snapshot in snapshots.items()}
    for key, entries in groups.items():
        while entries:
            identity, value = entries.pop(0)
            matches = [(sid, other) for sid, other in entries if other == value]
            if not matches:
                continue
            index = len(shared)
            shared.append(value)
            ids = {identity, *(sid for sid, _ in matches)}
            entries = [(sid, other) for sid, other in entries if sid not in ids]
            for sid in ids:
                copies[sid].pop(key)
                refs.setdefault(sid, {})[key] = index
    return {**result, 'snapshots': copies, 'sharedSnapshotValues': shared, 'snapshotRefs': refs}
