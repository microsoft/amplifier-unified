import asyncio
import unittest
from types import SimpleNamespace

from amplifier_web.execution_events import ExecutionEvents, public_usage
from amplifier_web.runtime import normalize_event


class ExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_compaction_calls_have_distinct_label_usage_and_no_public_stream(self):
        from amplifier_web.execution_events import CALL_PURPOSE
        events=ExecutionEvents('root',lambda value:None)
        events.turn_id='turn'
        class Provider:
            def get_info(self):return SimpleNamespace(id='openai',defaults={'model':'fixture'})
            async def complete(self,request):
                self.public_stream = not CALL_PURPOSE.get()
                return SimpleNamespace(usage={'input_tokens':20,'output_tokens':3})
            async def compact_context(self,request):
                self.public_stream = not CALL_PURPOSE.get()
                return {'message':{'opaque':'must not appear in diagnostics'},'usage':{'input_tokens':50,'output_tokens':5}}
        provider=events.instrument_provider('root',Provider())
        request=SimpleNamespace(model=None,metadata={'purpose':'context-compaction'})
        await provider.complete(request)
        self.assertFalse(provider.public_stream)
        await provider.compact_context(request)
        self.assertFalse(provider.public_stream)
        self.assertIsNone(CALL_PURPOSE.get())
        rows=list(events.nodes.values())
        self.assertEqual(len(rows),2)
        self.assertTrue(all(row['label']=='Context compaction' and row['turnId']=='turn' for row in rows))
        self.assertEqual(events.usage()['usage']['totalTokens'],78)
        self.assertNotIn('opaque',str(rows))
        await provider.complete(SimpleNamespace(model=None,metadata={}))
        self.assertTrue(provider.public_stream)
        self.assertEqual(list(events.nodes.values())[-1]['label'],'Model call')

    async def test_failed_compaction_restores_purpose_and_records_safe_failure(self):
        from amplifier_web.execution_events import CALL_PURPOSE
        events=ExecutionEvents('root',lambda value:None)
        class ContextLengthError(Exception):pass
        class Provider:
            def get_info(self):return SimpleNamespace(id='openai',defaults={'model':'fixture'})
            async def complete(self,request):raise ContextLengthError('secret raw request')
        provider=events.instrument_provider('root',Provider())
        with self.assertRaises(ContextLengthError):
            await provider.complete(SimpleNamespace(model=None,metadata={'purpose':'context-compaction'}))
        row=next(iter(events.nodes.values()))
        self.assertEqual(row['label'],'Context compaction')
        self.assertEqual(row['failure']['category'],'context_limit')
        self.assertNotIn('secret',str(row))
        self.assertIsNone(CALL_PURPOSE.get())

    async def test_concurrent_worker_calls_keep_original_turn_and_parent(self):
        emitted=[]
        events=ExecutionEvents('root',emitted.append)
        events.lifecycle({'type':'input.delivered','input_id':'turn-a'})
        events.hook('root','tool:pre',{'tool_call_id':'delegate-1','tool_name':'delegate','tool_input':{'secret':'private'}})
        events.lifecycle({'type':'child.updated','sessionId':'child','parentSessionId':'root','callId':'delegate-1','agent':'explorer','status':'running'})
        events.lifecycle({'type':'input.delivered','input_id':'turn-b'})
        async def call(sid, count):
            events.hook(sid,'llm:request',{'provider':'test','model':'fixture','raw':'private reasoning'})
            await asyncio.sleep(0)
            events.hook(sid,'llm:response',{'usage':{'input_tokens':count,'output_tokens':2,'cost_usd':'0.01'},'raw':'private response'})
        await asyncio.gather(call('child',3),call('root',7))
        rows=[row['event'] for row in emitted if row['event']['kind']=='llm' and row['event']['phase']=='completed']
        self.assertEqual(len(rows),2)
        child=next(row for row in rows if row['sessionId']=='child')
        self.assertEqual(child['turnId'],'turn-a')
        self.assertEqual(child['parentId'],'worker:child:delegate-1')
        self.assertEqual(events.children['child']['parentId'],'tool:root:delegate-1')
        self.assertEqual(events.usage()['usage']['totalTokens'],14)
        self.assertNotIn('private',str(emitted))
        normalized=normalize_event({'type':'execution.event','event':{**child,'raw':'private'}},'root')
        self.assertEqual(normalized[1]['rootSessionId'],'root')
        self.assertEqual(normalized[1]['sessionId'],'child')
        self.assertNotIn('raw',normalized[1])

    async def test_lifecycle_updates_do_not_double_count_cost(self):
        events=ExecutionEvents('root',lambda value:None)
        events.hook('root','llm:request',{})
        events.hook('root','provider:retry',{'error_message':'credential'})
        events.hook('root','llm:response',{'usage':{'input_tokens':3,'output_tokens':1}})
        self.assertEqual(events.usage()['calls'],1)
        self.assertEqual(events.usage()['usage']['costType'],'unavailable')
        self.assertIsNone(events.usage()['usage']['costUsd'])

    async def test_provider_boundary_pairs_hooks_dispatched_in_separate_tasks(self):
        events=ExecutionEvents('root',lambda value:None)
        events.lifecycle({'type':'input.delivered','input_id':'user-turn','source':'user'})
        events.lifecycle({'type':'input.delivered','input_id':'job-report','source':'live.job'})
        class Provider:
            def get_info(self):
                return SimpleNamespace(id='fixture',defaults={'model':'fixture'})
            async def complete(self,request,**kwargs):
                async def hook(name,data):events.hook('root',name,data)
                await asyncio.create_task(hook('llm:request',{}))
                await asyncio.sleep(0)
                await asyncio.create_task(hook('llm:response',{'usage':{'input_tokens':100,'output_tokens':2}}))
                return SimpleNamespace(usage={'input_tokens':100,'output_tokens':2,'cost_usd':'0.03'})
        provider=Provider()
        events.instrument_provider('root',provider)
        await asyncio.gather(provider.complete(SimpleNamespace(model=None)),provider.complete(SimpleNamespace(model='selected')))
        self.assertEqual(events.usage()['calls'],2)
        self.assertEqual(events.usage()['usage']['totalTokens'],204)
        self.assertTrue(all(row['turnId']=='user-turn' for row in events.nodes.values()))

    def test_invalid_usage_cannot_publish_nan_or_negative_counts(self):
        result=public_usage({'input_tokens':-3,'output_tokens':float('nan'),'cost_usd':'nan','secret':'no'})
        self.assertEqual(result,{'costType':'unavailable'})


class TruncationFailureTests(unittest.IsolatedAsyncioTestCase):
    """Offline typed provider boundary, not a reproduction of paid inference."""

    async def test_certified_call_projects_terminal_and_sanitized_report(self):
        import json
        from amplifier_module_provider_openai._response_handling import FunctionCallTruncationError
        from amplifier_module_loop_live.failures import ManagerTurnError, turn_failure
        from amplifier_web.feedback_diagnostics import failure_report
        from amplifier_web.session_health import generation_failure, terminal_exception_failure
        from amplifier_web.support_diagnostics import exception_chain
        error = FunctionCallTruncationError('PRIVATE arguments /private/path', provider='fixture')
        events = ExecutionEvents('root', lambda _: None)
        events.lifecycle({'type': 'generation.started', 'generation_id': 'g', 'initial_input_id': 'i'})
        events.lifecycle({'type': 'input.delivered', 'input_id': 'i'})
        class Provider:
            def get_info(self):
                return SimpleNamespace(id='fixture', defaults={})
            async def complete(self, request):
                raise error
        provider = events.instrument_provider('root', Provider())
        with self.assertRaises(FunctionCallTruncationError):
            await provider.complete(SimpleNamespace(model='fixture'))
        row = next(iter(events.nodes.values()))
        self.assertEqual((row['generationId'], row['admissionInputId'], row['phase']), ('g', 'i', 'error'))
        self.assertEqual(row['failure']['code'], 'function_call_truncated')
        native = turn_failure(error)
        wrapper = ManagerTurnError(native)
        wrapper.__cause__ = error
        session = {'id': 'root', 'status': 'error',
            'failure': {**generation_failure(native), 'generationId': 'g', 'inputIds': ['i'], 'recordedAt': 2},
            'generations': [{'event': 'generation.failed', 'generation_id': 'g', 'input_ids': ['i'], **native}]}
        capture = {'generationId': 'g', 'inputIds': ['i'], 'errorType': native['error_type'],
                   'exceptionChain': exception_chain(wrapper)}
        session['failure'] = terminal_exception_failure(session, capture)
        report = failure_report(session)
        self.assertEqual(report['code'], 'function_call_truncated')
        self.assertEqual(report['stage'], 'provider_request')
        self.assertEqual(report['nativeFailure'], {'category': 'unknown', 'errorType': 'LLMError',
                         'stage': 'provider_request', 'retryable': False})
        self.assertIs(report['retryable'], False)
        self.assertEqual(report['exceptionChain'][1]['errorType'], 'FunctionCallTruncationError')
        self.assertEqual(report['exceptionChain'][1]['providerFailure'], {'code': 'function_call_truncated'})
        self.assertNotIn('PRIVATE', json.dumps([row['failure'], session['failure'], report]))
        self.assertNotIn('/private/path', json.dumps(report))

    async def test_typed_call_failure_reaches_exact_terminal_and_opt_in_report(self):
        import json
        import os
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from amplifier_module_provider_openai._response_handling import FunctionCallTruncationError
        from amplifier_module_loop_live.failures import ManagerTurnError, turn_failure
        from amplifier_web.service import AppService
        from amplifier_web.support_diagnostics import exception_chain

        private = 'PRIVATE_PROVIDER_BODY_TOOL_ARGUMENTS_AND_PATH'
        error = FunctionCallTruncationError(private, provider='fixture', model=private)
        class Provider:
            calls = 0
            def get_info(self):
                return SimpleNamespace(id='fixture', defaults={'model': 'fixture'})
            async def complete(self, request):
                self.calls += 1
                raise error
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {
            key: str(Path(folder) / key) for key in (
                'AMPLIFIER_WEB_HOME', 'AMPLIFIER_HOME', 'AMPLIFIER_SESSION_STATE_HOME', 'AMPLIFIER_TERMINAL_HOME')
        }):
            app = AppService(Path(folder) / 'app', workspace=folder)
            try:
                await app.dispatch('session.create', {})
                session = app._session()
                app._message(session, 'user', 'Offline fixture', inputId='i')
                emitted = []
                events = ExecutionEvents(session['id'], emitted.append)
                start = {'type': 'generation.started', 'generation_id': 'g',
                         'initial_input_id': 'i', 'input_ids': ['i'], 'observedAt': 1}
                events.lifecycle(start)
                events.lifecycle({'type': 'input.delivered', 'input_id': 'i'})
                await app.on_runtime_event(*normalize_event(start, session['id']))
                provider = events.instrument_provider(session['id'], Provider())
                with self.assertRaises(FunctionCallTruncationError):
                    await provider.complete(SimpleNamespace(model='fixture'))
                for event in emitted:
                    await app.on_runtime_event(*normalize_event(event, session['id']))
                observed = next(row['event']['failure'] for row in emitted if row['event'].get('failure'))
                self.assertEqual(observed['code'], 'function_call_truncated')
                self.assertFalse(session.get('failure'))  # A failed call alone is not a failed turn.

                native = turn_failure(error)
                self.assertEqual((native['error_type'], native['error_category'], native['error_stage']),
                                 ('LLMError', 'unknown', 'provider_request'))
                terminal = {'type': 'generation.failed', 'generation_id': 'g',
                            'input_ids': ['i'], 'observedAt': 2, **native}
                await app.on_runtime_event(*normalize_event(terminal, session['id']))
                self.assertEqual(session['failure']['category'], 'unknown')
                recorded = session['failure']['recordedAt']
                wrapper = ManagerTurnError(native)
                wrapper.__cause__ = error
                supplement = {'type': 'runtime.error', 'terminalFailure': {
                    'generationId': 'g', 'inputIds': ['i'], 'errorType': native['error_type'],
                    'exceptionChain': exception_chain(wrapper)}}
                await app.on_runtime_event(*normalize_event(supplement, session['id']))
                # A later generic worker exit must not overwrite this exact diagnosis.
                await app.on_runtime_event('runtime.error', {'sessionId': session['id'],
                    'errorType': 'RuntimeError', 'error': 'The worker failed.'})
                failure = session['failure']
                self.assertEqual((failure['category'], failure['code'], failure['stage']),
                                 ('invalid_request', 'function_call_truncated', 'provider_request'))
                self.assertEqual(failure['errorType'], 'LLMError')
                self.assertEqual(failure['nativeFailure'], {'category': 'unknown', 'errorType': 'LLMError',
                    'stage': 'provider_request', 'retryable': False})
                self.assertEqual(failure['recordedAt'], recorded)
                self.assertEqual(failure['effects'], 'not_rolled_back')
                self.assertIs(failure['retryable'], False)
                self.assertIn('This incomplete call was not run.', failure['guidance'])
                self.assertIn('Earlier actions may already have had effects.', failure['guidance'])
                self.assertIn('explicitly ask for a smaller step or chunked output', failure['guidance'])
                self.assertNotIn('not sent', failure['guidance'])
                self.assertNotIn('recovery copy', failure['guidance'])

                context = {'sessionId': session['id'], 'generationId': 'g', 'inputId': 'i'}
                preview = await app.dispatch('feedback.diagnostics', {'failureContext': context})
                facts = preview['result']['diagnostics']
                report = facts['failureReport']
                self.assertEqual(report['code'], 'function_call_truncated')
                self.assertEqual(report['stage'], 'provider_request')
                self.assertIs(report['retryable'], False)
                self.assertEqual(report['nativeFailure'], failure['nativeFailure'])
                self.assertEqual(report['exceptionChain'][1]['errorType'], 'FunctionCallTruncationError')
                self.assertNotIn(private, json.dumps([failure, facts]))
                args = {'requestId': 'truncation-report', 'title': 'Fixture', 'body': 'Offline test',
                        'category': 'bug', 'includeDiagnostics': True, 'failureContext': context}
                app.feedback.accept(args)
                frozen = app.feedback.diagnostics({'requestId': args['requestId']})['diagnostics']
                session['failure'] = {'category': 'authentication'}
                self.assertEqual(app.feedback.diagnostics({'requestId': args['requestId']})['diagnostics'], frozen)
                self.assertEqual(provider.calls, 1)
                self.assertEqual(len(app.state['sessions']), 1)
                self.assertFalse(any(row.get('kind') == 'tool' for row in events.nodes.values()))
            finally:
                await app.close()

    def test_truncation_cannot_be_inferred_from_names_text_handled_cause_or_other_scope(self):
        import copy
        from amplifier_core.llm_errors import LLMError
        from amplifier_module_provider_openai._response_handling import FunctionCallTruncationError
        from amplifier_module_loop_live.failures import ManagerTurnError, turn_failure
        from amplifier_web.session_health import (
            exception_details, failure_details, generation_failure, request_call_failure, terminal_exception_failure)
        from amplifier_web.support_diagnostics import exception_chain, terminal_exception_capture
        error = FunctionCallTruncationError('PRIVATE', provider='fixture')
        native = turn_failure(error)
        wrapper = ManagerTurnError(native)
        wrapper.__cause__ = error
        capture = {'generationId': 'g', 'inputIds': ['i'], 'errorType': 'LLMError',
                   'exceptionChain': exception_chain(wrapper)}
        session = {'id': 'root', 'status': 'error',
            'generations': [{'event': 'generation.failed', 'generation_id': 'g',
                             'input_ids': ['i'], **native}],
            'failure': {**generation_failure(native), 'generationId': 'g', 'inputIds': ['i'], 'recordedAt': 2}}
        spoof = type('FunctionCallTruncationError', (Exception,), {
            '__module__': FunctionCallTruncationError.__module__})('function call output limit')
        handled = LLMError('unclassified', provider='fixture')
        handled.__context__ = error
        for unknown in (spoof, handled, LLMError('FunctionCallTruncationError: max output tokens')):
            with self.subTest(unknown=type(unknown).__name__):
                self.assertEqual(exception_details(unknown)['category'], 'unknown')
        self.assertEqual(failure_details('function call output limit', 'FunctionCallTruncationError')['category'], 'unknown')
        wrapper.__cause__ = handled  # The real truncation is deeper, previously handled.
        stale = {**capture, 'exceptionChain': exception_chain(wrapper)}
        self.assertEqual(terminal_exception_failure(session, stale)['category'], 'unknown')
        for change in ({'generationId': 'other'}, {'inputIds': ['other']}, {'errorType': 'ValueError'}):
            with self.subTest(change=change):
                self.assertIsNone(terminal_exception_failure(session, {**capture, **change}))
        stopped = {**session, 'status': 'stopped'}
        self.assertIsNone(terminal_exception_failure(stopped, capture))
        uncertified = copy.deepcopy(capture)
        uncertified['exceptionChain'][1].pop('providerFailure')
        self.assertIsNone(terminal_exception_capture(uncertified))
        row = {'sessionId': 'root', 'generationId': 'g', 'admissionInputId': 'i',
               'failure': exception_details(error)}
        for change in ({}, {'sessionId': 'child'}, {'generationId': 'other'},
                       {'admissionInputId': 'other'}, {'lifecycle': 'background'}, {'purpose': 'context_compaction'}):
            with self.subTest(scope=change):
                # Even a same-input observed subtype cannot explain a generic
                # native LLMError without its exact certified terminal capture.
                current = {**session, 'execution': {'nodes': [{**row, **change}]}}
                self.assertIsNone(request_call_failure(current, ['i'], 'g', 'LLMError'))


def test_worker_report_and_tool_failure_have_metadata_without_payload_copies():
    events=ExecutionEvents('root',lambda event:None)
    events.hook('root','tool:pre',{'tool_call_id':'call','tool_name':'bash','tool_input':{'command':'ls -la','token':'user data'}})
    events.hook('root','tool:post',{'tool_call_id':'call','tool_result':{'success':False,'error':'failure body'}})
    row=events.nodes['tool:root:call']
    assert row['phase']=='error' and row['liveObservation']
    assert all(key not in row for key in ('input','output','error','purpose'))
    events.lifecycle({'type':'child.updated','sessionId':'child','status':'completed','report':'Completed review.'})
    assert events.nodes['worker:child']['summary']=='Completed review.'


def test_streaming_hook_transports_lifecycle_without_capturing_alternate_content():
    events=ExecutionEvents('root',lambda event:None)
    arguments={'command':'python -m pytest -q','cwd':'/workspace'}
    events.hook('root','tool:pre',{'tool_call_id':'stream','tool_name':'bash','tool_input':arguments})
    events.hook('root','tool:post',{'tool_call_id':'stream','tool_name':'bash','tool_input':arguments,
        'result':{'success':False,'output':{'stdout':'line\n'*6000,'returncode':1}}})
    row=normalize_event({'type':'execution.event','event':events.nodes['tool:root:stream']},'root')[1]
    assert row['phase']=='error' and row['liveObservation']
    assert not any(key in row for key in ('input','output','error'))
    assert row['inputSummary'] == {'command': 'python -m pytest -q'}
    assert row['outcomeSummary'] == {'exitCode': 1}
    assert 'line' not in str(row)


def test_post_only_falsey_result_still_has_a_completed_lifecycle():
    events=ExecutionEvents('root',lambda event:None)
    events.hook('root','tool:post',{'tool_call_id':'missed-pre','tool_name':'bash','tool_input':{'command':'true'},'result':''})
    row=events.nodes['tool:root:missed-pre']
    assert row['phase']=='completed' and 'input' not in row and 'output' not in row


def test_usage_inspection_does_not_duplicate_large_tool_content():
    events=ExecutionEvents('root',lambda event:None)
    events.hook('root','tool:pre',{'tool_call_id':'call','tool_name':'fixture','tool_input':{'text':'x'*64000}})
    assert 'input' not in events.nodes['tool:root:call']
    assert 'input' not in events.usage()['trace'][0]


def test_repeated_tool_completion_preserves_end_time_and_retry_clears_old_result(monkeypatch):
    events=ExecutionEvents('root',lambda event:None)
    monkeypatch.setattr('amplifier_web.execution_events.time.time',lambda:10)
    events.hook('root','tool:pre',{'tool_call_id':'call','tool_name':'fixture','tool_input':{}})
    monkeypatch.setattr('amplifier_web.execution_events.time.time',lambda:12)
    result={'tool_call_id':'call','tool_result':{'success':False,'error':'failed once'}}
    events.hook('root','tool:post',result)
    monkeypatch.setattr('amplifier_web.execution_events.time.time',lambda:20)
    events.hook('root','tool:post',result)
    assert events.nodes['tool:root:call']['endedAt']==12
    events.hook('root','tool:pre',{'tool_call_id':'call','tool_name':'fixture','tool_input':{}})
    row=events.nodes['tool:root:call']
    assert row['phase']=='running' and row['startedAt']==10
    assert all(row.get(key) is None for key in ('endedAt','output','error'))
    from amplifier_web.execution import ingest
    session={}
    ingest(session,{**row,'phase':'error','endedAt':12,'error':'failed once'})
    ingest(session,row)
    assert session['execution']['nodes'][0]['endedAt'] is None
    assert session['execution']['nodes'][0].get('error') is None


async def test_unwrappable_provider_hook_fallback_preserves_background_lifecycle():
    from amplifier_web.execution_events import CALL_PURPOSE
    events=ExecutionEvents('root',lambda event:None)
    events.turn_id='foreground'
    token=CALL_PURPOSE.set({'label':'Auxiliary call','turnId':'earlier','lifecycle':'background'})
    try:
        events.hook('root','llm:request',{'model':'fixture'})
        pending=next(iter(events.nodes.values()))
        assert pending['lifecycle']=='background' and pending['turnId']=='earlier'
        events.hook('root','llm:response',{'usage':{'input_tokens':2,'output_tokens':1}})
        assert events.nodes[pending['id']]['phase']=='completed'
        assert events.nodes[pending['id']]['lifecycle']=='background'
    finally:CALL_PURPOSE.reset(token)


def test_usage_summary_includes_cache_writes_once_without_changing_reported_counts():
    from amplifier_web.execution import rollup
    usage=public_usage({'input_tokens':4,'output_tokens':2,'total_tokens':6,'cache_read_tokens':3,'cache_write_tokens':100})
    assert usage['inputTokens']==4 and usage['totalTokens']==6
    assert usage['grossInputTokens']==104 and usage['grossTotalTokens']==106
    total=rollup([{'usage':usage},{'usage':usage}])
    assert total['totalTokens']==12 and total['grossTotalTokens']==212
