"""Discriminating naming behavior through the installed hook; no inference."""
import json
from types import SimpleNamespace

import pytest
from amplifier_core.message_models import ChatResponse, TextBlock, Usage
from amplifier_web.host.naming import LiveSessionNaming
from amplifier_web.host.session import SelectedProvider
from amplifier_web.naming import read

from test_naming_lifecycle import community, mounted, native_home  # noqa: F401
from test_automatic_history import app_factory  # noqa: F401


def recorder(host, replies):
    """Capture the real outgoing request, not a replacement naming generator."""
    calls = []

    async def complete(request, **kwargs):
        calls.append((request, kwargs))
        reply = replies[min(len(calls) - 1, len(replies) - 1)]
        return ChatResponse(content=[TextBlock(text=json.dumps(reply))],
                            usage=Usage(input_tokens=1, output_tokens=1, total_tokens=2))

    host.provider.complete = complete
    return calls


def prompt_text(call):
    return str(call[0].messages[0].content)


async def turn(namer, number):
    namer.observe({'type': 'input.delivered', 'input_id': str(number), 'source': 'user'})
    namer.observe({'type': 'generation.finished', 'input_ids': [str(number)]})
    if namer.pending:
        await namer.pending


async def test_default_selected_model_keeps_utility_envelope(native_home, community):
    host = await mounted(community, native_home, 'model-identity', count=0)
    calls = recorder(host, [{'action': 'set', 'name': 'Fix Atlas OAuth callback'}])
    selected = SelectedProvider(host.provider, {'model': 'selected-model', 'effort': 'high',
                                                'max_output_tokens': 32000})
    original_get = host.co.get
    host.co.get = lambda key: SimpleNamespace(root_provider=selected) if key == 'orchestrator' else original_get(key)
    namer = LiveSessionNaming(host.co, native_home / 'app', lambda _: None)
    try:
        await namer.suggest()
        [(request, kwargs)] = calls
        assert request.model == 'selected-model'
        assert request.max_output_tokens == 256
        assert request.metadata['stream'] is False
        assert request.reasoning_effort != 'high'
        assert kwargs.get('reasoning_effort') != 'high'
        assert kwargs.get('extended_thinking') is False
        assert selected.selection == {'model': 'selected-model', 'effort': 'high', 'max_output_tokens': 32000}
    finally:
        await namer.close()


async def test_live_sample_retains_intent_and_excludes_injected_or_binary_content(native_home, community):
    host = await mounted(community, native_home, 'context', count=0)
    host.context.messages = [
        {'role': 'system', 'content': 'SYSTEM_PRIVATE_SENTINEL'},
        {'role': 'user', 'content': '<context_file paths="notes">INJECTED_SENTINEL</context_file>\n'
         '<system-reminder>REMINDER_SENTINEL</system-reminder>\nHelp with login.'},
        {'role': 'assistant', 'content': 'Which application and failure?'},
        {'role': 'user', 'content': [
            {'type': 'image', 'source': {'data': 'BINARY_SENTINEL' * 10000}},
            {'type': 'text', 'text': 'The Atlas OAuth callback loops after sign-in.'}]},
        *[{'role': 'tool', 'content': 'TOOL_SENTINEL ' * 2000} for _ in range(90)],
        {'role': 'user', 'content': 'The current goal is to fix the callback redirect in Atlas.'},
    ]
    calls = recorder(host, [{'action': 'set', 'name': 'Fix Atlas callback redirect'}])
    namer = LiveSessionNaming(host.co, native_home / 'app', lambda _: None)
    try:
        await turn(namer, 1)
        text = prompt_text(calls[0])
        assert 'Help with login.' in text
        assert 'Atlas OAuth callback loops' in text
        assert 'current goal is to fix' in text
        for value in ('SYSTEM_PRIVATE_SENTINEL', 'INJECTED_SENTINEL', 'REMINDER_SENTINEL',
                      'BINARY_SENTINEL', 'TOOL_SENTINEL'):
            assert value not in text
        assert len(text.encode('utf-8')) <= 24000
    finally:
        await namer.close()


async def test_clarification_refines_once_across_restart_not_every_input(native_home, community):
    host = await mounted(community, native_home, 'clarify', count=0)
    host.context.messages = [{'role': 'user', 'content': 'Help with login.'},
                             {'role': 'assistant', 'content': 'Which app?'}]
    calls = recorder(host, [
        {'action': 'set', 'name': 'Investigate login', 'description': 'Find the login failure.'},
        {'action': 'set', 'name': 'Fix Atlas OAuth callback loop', 'description': 'Fix the redirect loop.'},
        {'action': 'keep'},
    ])
    namer = LiveSessionNaming(host.co, native_home / 'app', lambda _: None)
    await turn(namer, 1)
    assert len(calls) == 1
    await namer.close()
    namer = LiveSessionNaming(host.co, native_home / 'app', lambda _: None, ['1'])
    try:
        host.context.messages.append({'role': 'user', 'content': 'The Atlas OAuth callback loops after sign-in.'})
        await turn(namer, 2)
        assert len(calls) == 2
        assert read(namer.directory)['name'] == 'Fix Atlas OAuth callback loop'
        assert 'clarif' in prompt_text(calls[1]).lower()
        await namer.close()
        namer = LiveSessionNaming(host.co, native_home / 'app', lambda _: None, ['1', '2'])
        for number, text in [(3, 'Use the existing callback handler.'), (4, 'Add a regression test.')]:
            host.context.messages.append({'role': 'user', 'content': text})
            await turn(namer, number)
        assert len(calls) == 2
        host.context.messages.append({'role': 'user', 'content': 'Check the finished fix.'})
        await turn(namer, 5)
        assert len(calls) == 3
    finally:
        await namer.close()


async def test_stable_update_has_keep_contract_and_does_not_write_or_publish(native_home, community):
    host = await mounted(community, native_home, 'stable', count=0)
    host.metadata.set_name('Fix Atlas OAuth callback', source='generated', description='Remove the redirect loop.')
    keep = {'action': 'keep', 'name': 'Misleading new title', 'description': 'Must not be saved.'}
    # Prove the installed parser recognized keep; rejection is not stability.
    parser = community.SessionNamingHook(host.co, community.SessionNamingConfig())
    assert parser._parse_response(json.dumps({'action': 'keep'}))['action'] == 'keep'
    assert parser._parse_response(json.dumps(keep)) == keep
    calls = recorder(host, [keep, {'action': 'set', 'name': 'Plan Atlas migration',
                                  'description': 'Move the Atlas database.'}])
    events = []
    namer = LiveSessionNaming(host.co, native_home / 'app', events.append, ['1', '2', '3', '4'])
    before = (namer.directory / 'metadata.json').read_bytes()
    try:
        host.context.messages.append({'role': 'user', 'content': 'The regression test is now passing.'})
        await turn(namer, 5)
        assert len(calls) == 1
        text = prompt_text(calls[0])
        assert 'keep' in text and 'set' in text
        assert 'Fix Atlas OAuth callback' in text and 'Remove the redirect loop.' in text
        assert 'routine' in text.lower()
        # Lifecycle attempt metadata may change; canonical title fields may not.
        after = read(namer.directory)
        old = json.loads(before)
        for key in ('name', 'name_revision', 'name_updated_at', 'name_generated_at',
                    'description', 'description_updated_at'):
            assert after.get(key) == old.get(key)
        assert not [event for event in events if event['type'] == 'session.naming']
        host.context.messages.append({'role': 'user', 'content': 'Stop the login work. Plan the Atlas database migration instead.'})
        namer.active = True
        namer.schedule(periodic=True)
        await namer.pending
        assert len(calls) == 2
        assert read(namer.directory)['name'] == 'Plan Atlas migration'
        assert read(namer.directory)['description'] == 'Move the Atlas database.'
        assert len([event for event in events if event['type'] == 'session.naming']) == 1
    finally:
        await namer.close()


async def test_unchanged_selected_context_does_not_spend_on_tool_chatter(native_home, community):
    host = await mounted(community, native_home, 'tool-chatter', count=0)
    calls = recorder(host, [{'action': 'set', 'name': 'Build orbits'}])
    namer = LiveSessionNaming(host.co, native_home / 'app', lambda _: None)
    try:
        await turn(namer, 1)
        host.context.messages.extend({'role': 'tool', 'content': f'log {n}'} for n in range(20))
        namer.active = True
        namer.schedule(periodic=True)
        if namer.pending:
            await namer.pending
        assert len(calls) == 1
    finally:
        await namer.close()


async def test_automatic_history_sample_includes_later_clarification(app_factory, tmp_path):
    from test_automatic_naming import imported, tick
    from amplifier_web.naming import directory_for

    app, naming, clock, calls = await imported(app_factory, tmp_path)
    session = app.directory.select('history_managed AND native_project IS NOT NULL', limit=1)[0]
    directory = directory_for(app.data_dir, session)
    # Use canonical native files, never fabricate the outgoing naming prompt.
    from amplifier_foundation.session.history import SessionHistoryStore
    history = SessionHistoryStore(directory)
    messages = [{'role': 'user', 'content': [
                    {'type': 'text', 'text': '<context_file>' + 'INJECTED_NATIVE ' * 1500},
                    {'type': 'text', 'text': '</context_file>Help with login.'}]},
                {'role': 'assistant', 'content': 'Which application?'},
                {'role': 'user', 'content': 'The Atlas OAuth callback loops.'},
                *[{'role': 'tool', 'content': 'TOOL_PAYLOAD'} for _ in range(80)],
                {'role': 'user', 'content': 'Fix the callback redirect in Atlas.'}]
    history.save(messages, history.load_metadata())
    original = (directory / 'transcript.jsonl').read_bytes()
    await tick(app, naming, clock)
    assert len(calls) == 1
    text = calls[0][1]
    assert 'Atlas OAuth callback loops' in text and 'Fix the callback redirect' in text
    assert 'Help with login.' in text and 'INJECTED_NATIVE' not in text
    assert 'TOOL_PAYLOAD' not in text
    assert (directory / 'transcript.jsonl').read_bytes() == original
    assert not app.runtime.started and not app.runtime.sent
    assert app._session(session['id'], hydrate=False)['historyLoaded'] is False


async def test_explicit_naming_role_overrides_selected_model_without_borrowing(native_home, community):
    host = await mounted(community, native_home, 'explicit-role')
    host.co.config['hooks'][0]['config']['model_role'] = 'fast'
    calls = recorder(host, [{'action': 'set', 'name': 'Build orbits'}])

    class Resolver:
        async def resolve(self, role):
            assert role == 'fast'
            return [SimpleNamespace(provider='fixture', model='naming-role-model', config={})]

    host.co.register_capability('model_role_resolver', Resolver())
    original_get = host.co.get
    host.co.get = lambda key: SimpleNamespace(
        root_provider=SelectedProvider(host.provider, {'model': 'conversation-model'})
    ) if key == 'orchestrator' else original_get(key)
    namer = LiveSessionNaming(host.co, native_home / 'app', lambda _: None)
    try:
        await namer.suggest()
        assert len(calls) == 1 and calls[0][0].model == 'naming-role-model'
    finally:
        await namer.close()


@pytest.mark.parametrize('target', ['missing', 'fixture'])
async def test_explicit_role_refuses_missing_or_ambiguous_instance(native_home, community, target):
    host = await mounted(community, native_home, 'role-refusal')
    host.co.config['hooks'][0]['config']['model_role'] = 'fast'
    calls = recorder(host, [{'action': 'set', 'name': 'Should not run'}])
    # Two accounts from the same provider family are not interchangeable.
    providers = {'fixture-personal': host.provider, 'fixture-work': host.provider}

    class Resolver:
        async def resolve(self, role):
            return [SimpleNamespace(provider=target, model='role-model', config={})]

    host.co.register_capability('model_role_resolver', Resolver())
    original_get = host.co.get
    host.co.get = lambda key: providers if key == 'providers' else original_get(key)
    namer = LiveSessionNaming(host.co, native_home / 'app', lambda _: None)
    try:
        with pytest.raises(ValueError):
            await namer.suggest()
        assert not calls
    finally:
        await namer.close()


async def test_unicode_structured_text_has_a_real_prompt_bound(native_home, community):
    host = await mounted(community, native_home, 'structured-bound', count=0)
    host.context.messages = [
        {'role': 'user', 'content': [{'type': 'text', 'text': '語' * 1000000},
                                   {'type': 'tool_result', 'content': 'PRIVATE_TOOL_BODY'}]},
        {'role': 'user', 'content': 'Plan an Atlas database migration.'},
    ]
    calls = recorder(host, [{'action': 'set', 'name': 'Plan Atlas database migration'}])
    namer = LiveSessionNaming(host.co, native_home / 'app', lambda _: None)
    try:
        await turn(namer, 1)
        text = prompt_text(calls[0])
        assert len(text.encode('utf-8')) <= 24000
        assert 'Plan an Atlas database migration.' in text
        assert 'PRIVATE_TOOL_BODY' not in text
    finally:
        await namer.close()


async def test_greeting_does_not_consume_early_refinement(native_home, community):
    host = await mounted(community, native_home, 'greeting', count=0)
    host.context.messages = [{'role': 'user', 'content': 'Fix the login problem.'}]
    calls = recorder(host, [{'action': 'set', 'name': 'Fix login'},
                            {'action': 'set', 'name': 'Fix Atlas OAuth redirect'}])
    namer = LiveSessionNaming(host.co, native_home / 'app', lambda _: None)
    try:
        await turn(namer, 1)
        host.context.messages.append({'role': 'user', 'content': 'Thanks!'})
        await turn(namer, 2)
        assert len(calls) == 1
        host.context.messages.append({'role': 'user', 'content': 'The Atlas OAuth redirect loops after sign-in.'})
        await turn(namer, 3)
        assert len(calls) == 2
    finally:
        await namer.close()


@pytest.mark.parametrize('first', ['greeting', 'defer'])
async def test_delayed_first_title_arms_refinement_at_accepted_count(native_home, community, first):
    host = await mounted(community, native_home, 'delayed-' + first, count=0)
    host.context.messages = [{'role': 'user', 'content': 'Hello!' if first == 'greeting' else 'Help me with a problem.'}]
    replies = ([{'action': 'defer'}] if first == 'defer' else []) + [
        {'action': 'set', 'name': 'Fix login'},
        {'action': 'set', 'name': 'Fix Atlas callback loop'},
        {'action': 'keep'},
    ]
    calls = recorder(host, replies)
    namer = LiveSessionNaming(host.co, native_home / 'app', lambda _: None)
    try:
        await turn(namer, 1)
        assert len(calls) == int(first == 'defer')
        assert not read(namer.directory).get('naming_refinement')
        host.context.messages.append({'role': 'user', 'content': 'Fix the login issue.'})
        await turn(namer, 2)
        assert read(namer.directory)['naming_refinement']['completed'] == 2
        assert read(namer.directory)['name'] == 'Fix login'
        await namer.close()
        namer = LiveSessionNaming(host.co, native_home / 'app', lambda _: None, ['1', '2'])
        host.context.messages.append({'role': 'user', 'content': 'The Atlas OAuth callback loops after sign-in.'})
        await turn(namer, 3)
        assert read(namer.directory)['name'] == 'Fix Atlas callback loop'
        assert read(namer.directory)['naming_refinement']['status'] == 'consumed'
        call_count = len(calls)
        await namer.close()
        namer = LiveSessionNaming(host.co, native_home / 'app', lambda _: None, ['1', '2', '3'])
        host.context.messages.append({'role': 'user', 'content': 'Use the existing callback handler.'})
        await turn(namer, 4)
        assert len(calls) == call_count
        host.context.messages.append({'role': 'user', 'content': 'Run the regression test.'})
        await turn(namer, 5)
        assert len(calls) == call_count + 1
        assert 'periodic review' in prompt_text(calls[-1])
    finally:
        await namer.close()


@pytest.mark.parametrize('tag', ['context_file', 'system-reminders'])
@pytest.mark.parametrize('structured', [False, True])
async def test_large_envelope_preserves_following_request(native_home, community, tag, structured):
    host = await mounted(community, native_home, 'large-envelope', count=0)
    prefix = f'<{tag}>' + 'INJECTED_PRIVATE ' * 1300 + f'</{tag}>'
    assert len(prefix.encode()) > 20 * 1024
    request = 'Fix the Atlas OAuth callback loop.'
    content = prefix + request
    if structured:
        # Split the opening and closing tags, not just text within the envelope.
        split = len(prefix) - 4
        content = [{'type': 'text', 'text': content[:8]},
                   {'type': 'image', 'data': 'PRIVATE_IMAGE'},
                   {'type': 'text', 'text': content[8:split]},
                   {'type': 'text', 'text': content[split:]}]
    host.context.messages = [{'role': 'user', 'content': content}]
    calls = recorder(host, [{'action': 'set', 'name': 'Fix Atlas callback loop'}])
    namer = LiveSessionNaming(host.co, native_home / 'app', lambda _: None)
    try:
        await turn(namer, 1)
        assert len(calls) == 1
        text = prompt_text(calls[0])
        assert request in text
        assert 'INJECTED_PRIVATE' not in text and 'PRIVATE_IMAGE' not in text
        assert len(text.encode()) <= 24000
    finally:
        await namer.close()


@pytest.mark.parametrize('structured', [False, True])
@pytest.mark.parametrize('failure', ['unclosed', 'mismatch', 'over-scan', 'over-blocks'])
async def test_invalid_or_over_bound_envelopes_do_not_reach_provider(native_home, community, structured, failure):
    from amplifier_web.naming_context import MAX_SCAN_BYTES, MAX_BLOCKS
    host = await mounted(community, native_home, 'envelope-refusal', count=0)
    content = '<system-reminders>INJECTED_PRIVATE'
    if failure == 'mismatch':
        content += '</context_file>HIDDEN_REQUEST'
    if failure == 'over-scan':
        content += 'x' * MAX_SCAN_BYTES + '</system-reminders>HIDDEN_REQUEST'
    if structured:
        content = [{'type': 'text', 'text': content[:10]},
                   {'type': 'text', 'text': content[10:]}]
        if failure == 'over-blocks':
            content += [{'type': 'text', 'text': 'INJECTED_PRIVATE'}] * MAX_BLOCKS
            content += [{'type': 'text', 'text': '</system-reminders>HIDDEN_REQUEST'}]
    host.context.messages = [{'role': 'user', 'content': 'Fix Atlas login.'},
                             {'role': 'user', 'content': content}]
    calls = recorder(host, [{'action': 'set', 'name': 'Fix Atlas login'}])
    namer = LiveSessionNaming(host.co, native_home / 'app', lambda _: None)
    try:
        await turn(namer, 1)
        assert len(calls) == 1
        text = prompt_text(calls[0])
        assert 'Fix Atlas login.' in text
        assert 'INJECTED_PRIVATE' not in text and 'HIDDEN_REQUEST' not in text
        assert len(text.encode()) <= 24000
    finally:
        await namer.close()


def test_sampler_negative_control_exposes_old_injected_and_structured_payload(community):
    from amplifier_web.naming_context import sample
    rows = [
        {'role':'user','content':'<context_file paths="note">INJECTED_SECRET</context_file> Fix Atlas login.'},
        {'role':'user','content':[{'type':'image','data':'IMAGE_SECRET'*10000},
                                 {'type':'text','text':'Repair the OAuth callback.'}]},
    ]
    old = community.SessionNamingHook(None, community.SessionNamingConfig())._extract_naming_context(rows,None,None)
    new = sample(rows).text
    assert 'INJECTED_SECRET' in old and 'IMAGE_SECRET' in old
    assert 'INJECTED_SECRET' not in new and 'IMAGE_SECRET' not in new
    assert 'Repair the OAuth callback.' in new


@pytest.mark.parametrize('failure', ['empty-role', 'resolver-error', 'missing-resolver', 'bounded-transport'])
async def test_unavailable_explicit_route_never_falls_back(native_home, community, failure):
    host = await mounted(community, native_home, 'route-failure', count=0)
    calls = recorder(host,[{'action':'set','name':'Must not run'}])
    if failure == 'bounded-transport':
        host.provider.config = {'auth_mode':'chatgpt_plan'}
    else:
        # An explicit role must not silently become an expensive/default call.
        host.co.config['hooks'] = [{'module':'hooks-session-naming','config':{'model_role':'fast'}}]
        async def handler(event, data):
            from amplifier_core import HookResult
            return HookResult()
        host.co.hooks.register('prompt:complete',handler,name='session-naming')
        host.co.hooks.register('session:end',handler,name='session-naming-drain')
        if failure != 'missing-resolver':
            class Resolver:
                async def resolve(self, role):
                    if failure == 'resolver-error':
                        raise ValueError('Synthetic resolver unavailable')
                    return []
            host.co.register_capability('model_role_resolver',Resolver())
    namer = LiveSessionNaming(host.co,native_home/'app',lambda _:None)
    try:
        with pytest.raises(ValueError):
            await namer.suggest()
        assert not calls
    finally:
        await namer.close()


async def test_explicit_goal_change_updates_title_description_and_manual_choice_wins(native_home, community):
    host = await mounted(community,native_home,'goal-change',count=0)
    host.metadata.set_name('Debug cache expiry',source='generated',description='Find the Redis expiry issue.')
    calls = recorder(host,[{'action':'set','name':'Plan database migration',
                           'description':'Plan the Atlas database migration.'}])
    events=[]
    namer=LiveSessionNaming(host.co,native_home/'app',events.append,['1','2','3','4'])
    try:
        host.context.messages.append({'role':'user','content':'Stop cache work. Plan the Atlas database migration instead.'})
        await turn(namer,5)
        current=read(namer.directory)
        assert current['name']=='Plan database migration'
        assert current['description']=='Plan the Atlas database migration.'
        assert 'Stop cache work' in prompt_text(calls[0])
        assert len([e for e in events if e['type']=='session.naming'])==1
        host.metadata.set_name('My migration checklist')
        for n in range(6,11):
            host.context.messages.append({'role':'user','content':f'Review migration phase {n}.'})
            await turn(namer,n)
        assert len(calls)==1 and read(namer.directory)['name']=='My migration checklist'
    finally:
        await namer.close()


def test_saved_sample_bounds_and_fences_native_sources(tmp_path):
    from amplifier_web.naming_context import saved_sample
    from amplifier_web.transcript_window import READ_LIMIT
    owner={'project':'synthetic','nativeId':'bounded'}
    path=tmp_path/'transcript.jsonl'
    rows=[{'role':'user','content':'Investigate Atlas login.'},
          {'role':'user','content':'The OAuth callback loops.'},
          *[{'role':'tool','content':'payload '*1000} for _ in range(2000)],
          {'role':'user','content':'Fix the redirect callback in Atlas.'}]
    path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
    assert path.stat().st_size>READ_LIMIT
    result=saved_sample(tmp_path,owner)
    assert 'Investigate Atlas login.' in result.text
    assert 'OAuth callback loops.' in result.text
    assert 'Fix the redirect callback' in result.text
    assert 'payload' not in result.text and len(result.text.encode())<=8192
    path.unlink()
    other=tmp_path/'outside'
    other.write_text('{"role":"user","content":"Do not read"}\n')
    path.symlink_to(other)
    with pytest.raises(ValueError):
        saved_sample(tmp_path,owner)
