"""Shared, stdlib-only identity of a qualified test runner execution route."""
import json
import hashlib
from pathlib import Path

# These historical installations predate per-install runtime qualification.
# New purpose-created updates always require a retained dynamic proof.
STATIC_CORE_COMMITS = frozenset({
    '8f644b3d8d8b89f0bb3ae9853bb531017dc98a04',
    '5235833098d9a630791452a46558d92f58af0389',
})


def execution_binding(config):
    value = json.loads(json.dumps(config))
    for field in ('maxRunning', 'maxInstances', 'minFreeBytes'):
        value.pop(field, None)
        if isinstance(value.get('incus'), dict):
            value['incus'].pop(field, None)
    return value


def selection(directory, config, read):
    """Find a retained source declaration despite intended mutable limits."""
    bound = execution_binding(config)
    def key(value):
        return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
    direct = [Path(directory) / ('selection-' + key(value) + '.json') for value in (config, bound)]
    paths = [*direct, *sorted(Path(directory).glob('selection-*.json'))[:256]]
    for path in dict.fromkeys(paths):
        item = read(path)
        if isinstance(item, dict) and execution_binding(item.get('configuration', {})) == bound:
            return item
    return None


def rollback_record(directory, config, read):
    selected = selection(directory, config, read)
    if not selected or not isinstance(selected.get('updateId'), str):
        return None
    import re
    if not re.fullmatch('[a-f0-9]{32}', selected['updateId']):
        return None
    original = selected.get('coreConfiguration', config)
    if not isinstance(original, dict):
        return None
    # Engine-only composition preserves the original core update ancestry. No
    # other setting or backend change may borrow that rollback relationship.
    if execution_binding({k: v for k, v in original.items() if k != 'incus'}) != execution_binding({k: v for k, v in config.items() if k != 'incus'}):
        return None
    record = read(Path(directory) / (selected['updateId'] + '.json'))
    return record if (record and record.get('state') == 'selected'
        and execution_binding(record.get('candidate', {})) == execution_binding(original)) else None
