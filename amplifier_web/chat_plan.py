"""Read-only Plan projection from successful, canonical todo receipts."""
import json


def latest_plan(nodes, aliases, read_field):
    candidates = [row for row in nodes if row.get('kind') == 'tool'
                  and row.get('sessionId') in aliases
                  and row.get('label') == 'todo' and row.get('phase') == 'completed']
    if not candidates:
        return None
    node = max(candidates, key=lambda row: (row.get('endedAt') or 0, row.get('eventOrder', 0)))
    result = {'id': node['id'], 'turnId': node.get('turnId'), 'updatedAt': node.get('endedAt')}

    def field(name):
        reference = node.get('_eventFields', {}).get(name)
        if reference and reference.get('bytes', 0) > 1_000_000:
            raise ValueError('Oversized recorded plan')
        value = read_field(reference) if reference else node.get(name)
        return json.loads(value) if isinstance(value, str) else value

    try:
        output = field('output')
        if not isinstance(output, dict) or output.get('success') is False:
            raise ValueError('Missing successful result')
        output = output.get('output', output)
        if not isinstance(output, dict):
            raise ValueError('Invalid todo result')
        todos = output.get('todos')
        if todos is None and output.get('status') in {'created', 'updated'}:
            # todo.update returns counts; only a successful receipt authorizes
            # displaying the accepted input list. Never show proposed/failed input.
            args = field('input')
            if isinstance(args, dict) and args.get('action') in {'create', 'update'}:
                todos = args.get('todos')
        if not isinstance(todos, list) or len(todos) > 200:
            raise ValueError('Missing or oversized list')
        items = []
        for item in todos:
            if not isinstance(item, dict) or item.get('status') not in {'pending', 'in_progress', 'completed'}:
                raise ValueError('Invalid task')
            if not isinstance(item.get('content'), str) or not isinstance(item.get('activeForm', ''), str):
                raise ValueError('Invalid task text')
            if len(item['content']) > 4000 or len(item.get('activeForm', '')) > 4000:
                raise ValueError('Oversized task')
            items.append({key: item[key] for key in ('content', 'status', 'activeForm') if key in item})
        if sum(len(item['content']) + len(item.get('activeForm', '')) for item in items) > 16000:
            raise ValueError('Oversized plan')
        return {**result, 'items': items}
    except (ValueError, KeyError, TypeError, OSError):
        # Do not substitute an older list and present it as the latest one.
        return {**result, 'unavailable': True}
