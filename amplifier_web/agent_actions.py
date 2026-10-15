"""Byte-bounded discovery of exact action schemas, independent of execution."""
import hashlib
import json

PAGE_BYTES = 12000


def encoded(value):
    # Bound escaped JSON too: programmatic callers often text() the whole receipt.
    return json.dumps(value, ensure_ascii=True, separators=(',', ':'))


def read_actions(actions, args):
    prefix = args.get('prefix', '')
    if not isinstance(prefix, str):
        raise ValueError('Action prefix must be text.')
    rows = sorted((row for row in actions if row['name'].startswith(prefix)), key=lambda row: row['name'])
    revision = hashlib.sha256(encoded(rows).encode()).hexdigest()
    if args.get('revision') is not None and args['revision'] != revision:
        raise ValueError('Action catalog changed. Read the first page before continuing.')
    name = args.get('name')
    if name is not None:
        if not isinstance(name, str):
            raise ValueError('Action name must be text.')
        row = next((row for row in rows if row['name'] == name), None)
        if row is None:
            raise ValueError('That action is not in this catalog.')
        offset = args.get('schemaOffset', 0)
        if type(offset) is not int or offset < 0:
            raise ValueError('Use a nonnegative integer schemaOffset.')
        content = encoded(row)
        end = min(len(content), offset + 3000)
        return {'prefix': prefix, 'revision': revision, 'name': name,
                'schemaText': content[offset:end], 'schemaOffset': offset,
                'nextSchemaOffset': end if end < len(content) else None,
                'totalSchemaCharacters': len(content),
                'note': 'Concatenate schemaText pages, then parse the complete JSON action. This reads schemas only.'}
    offset, limit = args.get('offset', 0), args.get('limit', 20)
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError('Use a nonnegative integer offset and limit between 1 and 50.')
    result = {'prefix': prefix, 'revision': revision, 'offset': offset,
              'items': [], 'total': len(rows), 'nextOffset': None}
    for row in rows[offset:offset + limit]:
        item = row
        if len(encoded(row)) > PAGE_BYTES - 1000:
            item = {'name': row['name'], 'description': row['description'][:300],
                    'schemaRead': {'prefix': prefix, 'revision': revision, 'name': row['name'], 'schemaOffset': 0},
                    'note': 'Schema exceeds a catalog page. Read every schemaText page before dispatch.'}
        candidate = {**result, 'items': [*result['items'], item]}
        if result['items'] and len(encoded(candidate)) > PAGE_BYTES - 200:
            break
        result['items'].append(item)
    end = offset + len(result['items'])
    result['nextOffset'] = end if end < len(rows) else None
    return result
