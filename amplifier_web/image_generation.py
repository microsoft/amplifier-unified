"""Compact image work evidence; never a generation command or retry owner."""


def observe(node, event, data):
    if node.get('label') != 'image_generate':
        return
    if event == 'tool:pre':
        node.pop('imageGeneration', None)
        args = next((data[key] for key in ('tool_input', 'arguments', 'input') if key in data), None)
        if not isinstance(args, dict) or args.get('action') not in {'generate', 'edit'}:
            return
        identity = args.get('request_id')
        if isinstance(identity, str) and 0 < len(identity) <= 500:
            node['imageGeneration'] = {'requestId': identity, 'operation': args['action']}
    elif node.get('imageGeneration'):
        result = data.get('result', data.get('tool_result'))
        if hasattr(result, 'model_dump'):
            result = result.model_dump()
        output = result.get('output', result) if isinstance(result, dict) else None
        status = output.get('status') if isinstance(output, dict) else None
        failed = event == 'tool:error' or isinstance(result, dict) and result.get('success') is False
        node['imageGeneration'] = {**node['imageGeneration'],
            'outcome': 'error' if failed or status == 'failed' else 'completed' if status == 'completed' else 'unknown'}


def project(session, tree):
    """Only proven root-turn anchors enter the transcript's compact projection."""
    aliases = {session.get(key) for key in ('id', 'nativeIdentity', 'runtimeSessionId') if session.get(key)}
    turns = {turn['id']: turn for turn in tree.get('turns', [])}
    messages = {row.get('id') for row in session.get('messages', [])}
    result = []
    for node in tree.get('nodes', []):
        image = node.get('imageGeneration')
        if (not isinstance(image, dict) or node.get('sessionId') not in aliases
                or image.get('operation') not in {'generate', 'edit'}
                or not isinstance(image.get('requestId'), str) or not 0 < len(image['requestId']) <= 500):
            continue
        turn = turns.get(node.get('turnId'), {})
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
                       'requestId': image['requestId'], 'operation': image['operation']})
    return result[-32:]
