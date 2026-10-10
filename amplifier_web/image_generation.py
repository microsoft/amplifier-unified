"""Compact image work evidence; never a generation command or retry owner."""


def metadata(value):
    if (not isinstance(value, dict) or value.get('operation') not in ('generate', 'edit')
            or not isinstance(value.get('requestId'), str) or not 0 < len(value['requestId']) <= 500):
        return None
    result = {key: value[key] for key in ('requestId', 'operation')}
    if value.get('outcome') in ('completed', 'error', 'unknown'):
        result['outcome'] = value['outcome']
    return result


def observe(node, event, data):
    if node.get('label') not in {'image_generate', 'nano-banana'}:
        return
    if event == 'tool:pre':
        node.pop('imageGeneration', None)
        args = next((data[key] for key in ('tool_input', 'arguments', 'input') if key in data), None)
        if not isinstance(args, dict):
            return
        operation = args.get('action') if node['label'] == 'image_generate' else args.get('operation')
        if operation not in ('generate', 'edit'):
            return
        identity = args.get('request_id') if node['label'] == 'image_generate' else node.get('id')
        if isinstance(identity, str) and 0 < len(identity) <= 500:
            node['imageGeneration'] = {'requestId': identity, 'operation': operation}
    elif node.get('imageGeneration'):
        result = data.get('result', data.get('tool_result'))
        if hasattr(result, 'model_dump'):
            result = result.model_dump()
        output = result.get('output', result) if isinstance(result, dict) else None
        status = output.get('status') if isinstance(output, dict) else None
        if (node['label'] == 'nano-banana' and isinstance(output, dict)
                and isinstance(output.get('generated_images'), list)
                and output['generated_images'] and all(isinstance(path, str) and path for path in output['generated_images'])):
            status = 'completed'
        failed = event == 'tool:error' or isinstance(result, dict) and result.get('success') is False
        node['imageGeneration'] = {**node['imageGeneration'],
            'outcome': 'error' if failed or status == 'failed' else 'completed' if status == 'completed' else 'unknown'}


def project(session, tree):
    """Only proven root-turn anchors enter the transcript's compact projection."""
    aliases = {session.get(key) for key in ('id', 'nativeIdentity', 'runtimeSessionId') if session.get(key)}
    turns = {turn['id']: turn for turn in tree.get('turns', [])}
    messages = {row.get('id') for row in session.get('messages', [])}
    result = []
    nodes = {node['id']: node for node in tree.get('nodes', [])}
    def root_turn(node):
        # Delegated tools belong here only through a recorded parent chain.
        seen = set()
        while node and node.get('id') not in seen:
            seen.add(node.get('id'))
            if node.get('sessionId') in aliases:
                return turns.get(node.get('turnId'), {})
            node = nodes.get(node.get('parentId'))
        return {}
    for node in tree.get('nodes', []):
        image = metadata(node.get('imageGeneration'))
        if not image:
            continue
        turn = root_turn(node)
        mid = turn.get('anchorMessageId')
        if mid not in messages or mid is None:
            continue
        running = (node.get('phase') == 'running' and not node.get('endedAt')
                   and turn.get('phase') in {'running', 'working', 'starting'} and not turn.get('endedAt')
                   and session.get('status') in {'working', 'running', 'starting'})
        phase = 'running' if running else image.get('outcome', 'interrupted')
        if phase not in {'running', 'completed', 'error', 'unknown', 'interrupted'}:
            phase = 'unknown'
        result.append({'id': node['id'], 'messageId': mid, 'phase': phase,
                       'requestId': image['requestId'], 'operation': image['operation'],
                       **({'resultError': True} if session.get('imageResultErrors', {}).get(node['id']) else {})})
    return result[-32:]
