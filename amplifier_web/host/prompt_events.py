"""Supply the host-owned prompt completion event that app-cli emits after execute.

loop-live keeps execute open across turns, so its underlying orchestrator's final
turn event is the completion boundary. The orchestrator already emits submit.
"""


def install(coordinator):
    from amplifier_core import HookResult

    if coordinator.get_capability('web.prompt_completion'):
        return
    coordinator.register_capability('web.prompt_completion', True)
    current = {}

    async def observe(event, data):
        if data.get('session_id', coordinator.session_id) != coordinator.session_id:
            return HookResult()
        if event == 'prompt:submit':
            current.update(prompt=data.get('prompt', ''), completed=False)
        elif event == 'prompt:complete':
            current['completed'] = True
        elif (current and not current.get('completed') and data.get('goal_final', True)
              and data.get('status') not in {'error', 'cancelled'}):
            messages = await coordinator.get('context').get_messages()
            last = messages[-1] if messages else {}
            response = last.get('content', '') if last.get('role') == 'assistant' and not last.get('tool_calls') else ''
            if isinstance(response, list):
                response = ''.join(block.get('text', '') for block in response
                                   if isinstance(block, dict) and block.get('type') in {'text', 'output_text'})
            current['completed'] = True
            # Completion hooks may ask the mounted provider to evaluate the
            # response. Their output is auxiliary, while usage stays observable.
            from ..execution_events import CALL_PURPOSE
            token = CALL_PURPOSE.set({**(CALL_PURPOSE.get() or {}),
                'label': 'Completion hook', 'purpose': 'prompt_completion'})
            try:
                await coordinator.hooks.emit('prompt:complete', {
                    'session_id': coordinator.session_id, 'prompt': current.get('prompt', ''),
                    'response': response,
                })
            finally:
                CALL_PURPOSE.reset(token)
        return HookResult()

    for event in ('prompt:submit', 'prompt:complete', 'orchestrator:complete'):
        coordinator.hooks.register(event, observe, name='unified-prompt-' + event, priority=900)
