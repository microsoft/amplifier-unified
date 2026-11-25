"""Compare loaded plans using the worker's qualified source provenance.

Only module declarations may relocate. Configuration values and arbitrary paths
remain exact; a similar cache name is never evidence that the code is the same.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import re

from .host.components import declarations


def _normalized(value, aliases):
    from .runtime_controls import PLAN_KEYS, SECTIONS, public_config

    value = {key: copy.deepcopy(item) for key, item in public_config(value).items() if key in PLAN_KEYS}
    for section in SECTIONS:
        for row in value.setdefault(section, []):
            row.setdefault('enabled', True)
    for row in declarations(value):
        if row.get('source') in aliases:
            row['source'] = aliases[row['source']]
    return value


def matches(expected, actual, *, sources=None):
    """Normalize only public redaction, default enabled rows and proven sources."""
    from .configuration_authority import fingerprint

    # Redacted public equality is not proof of the underlying secret bytes.
    return fingerprint(_normalized(expected, sources or {})) == fingerprint(_normalized(actual, {}))


def mismatch_summary(expected, actual, *, sources=None):
    """Fixed section/category labels only; never values, names, keys or digests.

    This explains failed public-plan equality, not secret equality or the cause
    of a source difference. It cannot authorize relocation or change a verdict.
    At most eight section entries with six categories each can be returned.
    """
    from .configuration_authority import fingerprint

    expected, actual = _normalized(expected, sources or {}), _normalized(actual, {})
    rows = {id(row) for row in [*declarations(expected), *declarations(actual)]}

    def kinds(left, right):
        if fingerprint(left) == fingerprint(right):
            return set()
        if type(left) is not type(right):
            return {'type'}
        if isinstance(left, dict):
            missing = left.keys() ^ right.keys()
            special = missing & {'source', 'enabled'} if id(left) in rows or id(right) in rows else set()
            found = {'presence'} if missing - special else set()
            found.update('unproved_source' if key == 'source' else 'enabled' for key in special)
            for key in left.keys() & right.keys():
                if id(left) in rows and key in {'source', 'enabled'}:
                    if fingerprint(left[key]) != fingerprint(right[key]):
                        found.add('unproved_source' if key == 'source' else 'enabled')
                else:
                    found.update(kinds(left[key], right[key]))
            return found
        if isinstance(left, list):
            if sorted(map(fingerprint, left)) == sorted(map(fingerprint, right)):
                return {'order'}
            found = {'presence'} if len(left) != len(right) else set()
            for before, after in zip(left, right):
                found.update(kinds(before, after))
            return found
        return {'value'}

    result = []
    # Explicit labels: never interpolate a plan-controlled key, even below agents.
    for section in ('providers', 'tools', 'hooks', 'session', 'agents', 'spawn', 'context', 'instruction'):
        if (section in expected) != (section in actual):
            categories = {'presence'}
        else:
            categories = kinds(expected.get(section), actual.get(section))
        if categories:
            result.append(f"{section} ({', '.join(sorted(categories))})")
    return '; '.join(result)


def source_aliases(home, worker, expected, actual):
    """Read bounded qualification receipts for this exact worker's generation.

    Sources must be identical, or match the already adopted component update for
    the same mutable source choice. Never trust caller-supplied path mappings.
    Missing or ambiguous evidence leaves the original source comparison intact.
    """
    aliases = {}
    generation, selected = worker.get('generation'), worker.get('source_generation')
    if not generation or not selected or selected == generation:
        return aliases
    home = Path(home)
    base = home / 'updates/releases'
    if any(not isinstance(value, str) or Path(value).name != value or value in {'.', '..'}
           for value in (generation, selected)):
        return aliases
    try:
        target = base / selected
        parent = base / generation
        if json.loads((target / 'validated.json').read_text()).get('parentGeneration') != generation:
            return aliases
        if not (target / 'profiles-qualified.json').is_file():
            return aliases

        def records(root):
            rows = json.loads((root / 'runtime-installed.json').read_text())
            found = {}
            for row in rows:
                source = row.get('cacheSource') or {}
                if (not row.get('path') or source.get('dirty') is not False
                        or not source.get('url') or not source.get('ref')
                        or not re.fullmatch('[a-f0-9]{40}', source.get('revision', ''))
                        or not re.fullmatch('[a-f0-9]{64}', source.get('trackedContentSha256', ''))):
                    continue
                path = Path(row['path'])
                if not path.is_relative_to(base) or '..' in path.parts:
                    continue
                parts = path.relative_to(base).parts
                if len(parts) < 4 or parts[1:3] != ('foundation', 'cache'):
                    continue
                # A qualified environment can retain an older generation's
                # editable paths. This receipt owns that exact installed graph;
                # only its foundation-relative declaration is relocated.
                relative = Path(*parts[2:])
                identity = (row['name'], source['url'], source['ref'], source['revision'],
                            source.get('subdirectory'), source['trackedContentSha256'])
                found[relative] = identity if relative not in found else None
            return found

        if not (parent / 'validated.json').is_file() or not (parent / 'profiles-qualified.json').is_file():
            return aliases
        from .updates import pinned

        adopted = records(parent)
        previous = {generation: adopted}
        new = records(target)
        loaded = {row.get('source') for row in declarations(actual)}
        for row in declarations(expected):
            source = row.get('source')
            if (not isinstance(source, str) or '..' in Path(source).parts
                    or not Path(source).is_relative_to(base)):
                continue
            parts = Path(source).relative_to(base).parts
            if len(parts) < 4 or parts[1:3] != ('foundation', 'cache'):
                continue
            prior = parts[0]
            if prior not in previous:
                root = base / prior
                # A saved edit can outlive multiple component updates. Its old
                # qualification still proves which source choice was saved.
                if (not (root / 'profiles-qualified.json').is_file()
                        or not isinstance(json.loads((root / 'validated.json').read_text()), dict)):
                    continue
                previous[prior] = records(root)
            old = previous[prior]
            relative = Path(*parts[2:])
            replacement = str(target / 'foundation' / relative)
            if replacement not in loaded:
                continue
            candidates = [path for path in old if relative == path or relative.is_relative_to(path)]
            if not candidates:
                continue
            path = max(candidates, key=lambda path: len(path.parts))
            before, after = old[path], new.get(path)
            if before is None or after is None:
                continue
            same_choice = before[:3] == after[:3] and before[4] == after[4]
            # Runtime.start captured this qualified parent for the exact
            # worker. A later global promotion cannot change its loaded graph.
            adopted_update = (same_choice and not pinned(before[2])
                              and after == adopted.get(path))
            if before == after or adopted_update:
                aliases[source] = replacement
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {}
    return aliases


async def observe_loaded(management, session, guard):
    """Inspect the captured existing owner; never prepare or start a worker."""
    runtime, sid = management.service.runtime, session['id']
    workers = getattr(runtime, 'workers', {})
    row = workers.get(sid)
    interruption = session.get('interruptionRevision', 0)
    def ownership():
        return tuple(row.get(key) for key in ('runtimeOwnerId', 'generation', 'source_generation')) if row is not None else None
    owner = ownership()

    def same_owner():
        guard()
        if (workers.get(sid) is not row or ownership() != owner
                or management.service._session(sid).get('interruptionRevision', 0) != interruption):
            raise ValueError('The loaded configuration owner changed before readback completed.')

    same_owner()
    inspected = await runtime.control(sid, 'configuration.inspect', {}, guard=same_owner)
    same_owner()
    if not isinstance(inspected.get('plan'), dict):
        raise ValueError('Saved configuration has no confirmed loaded-plan readback.')
    return inspected, same_owner


async def inspect_loaded(management, session, expected, guard):
    inspected, same_owner = await observe_loaded(management, session, guard)
    row = getattr(management.service.runtime, 'workers', {}).get(session['id'])
    sources = source_aliases(management.service.data_dir, row or {}, expected, inspected['plan'])
    if not matches(expected, inspected['plan'], sources=sources):
        summary = mismatch_summary(expected, inspected['plan'], sources=sources)
        raise ValueError('Saved configuration does not match loaded-plan readback. '
                         f'Its operation remains unresolved. Mismatch categories: {summary}.')
    return inspected, same_owner
