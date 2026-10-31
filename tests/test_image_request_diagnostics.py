"""Each prepared request reports its own image content, never rewrites history."""
import asyncio
import json

import pytest

from test_output_image_pairs import pair, runtime, pixels


def observation(request):
    content = request.messages[-1].content
    text = content if isinstance(content, str) else content[0].text
    return json.loads(text.split('\n', 1)[1]), text


def reason(value, single):
    return value['reasonCode'] if single else value['omitted'][0]['reasonCode']


@pytest.mark.parametrize('single', [False, True])
@pytest.mark.parametrize('error,code', [
    (TimeoutError('injected read deadline'), 'image_read_timeout'),
    (ValueError('injected unavailable source'), 'image_read_unavailable'),
])
async def test_later_read_failure_does_not_rewrite_earlier_request(runtime, monkeypatch, single, error, code):
    r = runtime;await r.read(single=single)
    earlier = await r.provider.complete(r.request())
    expected = r.pair.data[:1] if single else r.pair.data
    assert pixels(earlier) == expected
    saved = earlier.model_dump_json()
    original = r.pair.app.app_bridge
    async def bridge(operation, args, sid):
        if operation.startswith('outputs.image'):raise error
        return await original(operation, args, sid)
    monkeypatch.setattr(r.pair.app, 'app_bridge', bridge)
    later = await r.provider.complete(r.request())
    value, text = observation(later)
    assert pixels(later) == [] and reason(value, single) == code
    assert value['stage'] == 'request_preparation'
    assert value['toolCallId'] == observation(earlier)[0]['toolCallId'] == 'image-call-1'
    assert 'only to this model request' in text
    assert 'does not invalidate an earlier inspection' in text
    assert 'hashes changed' not in text and 'no longer matches' not in text
    assert earlier.model_dump_json() == saved
    assert pixels(earlier) == expected


@pytest.mark.parametrize('single', [False, True])
@pytest.mark.parametrize('change,code', [
    ('read_timeout', 'image_read_timeout'), ('read_error', 'image_read_unavailable'),
    ('context_timeout', 'context_read_timeout'), ('context_error', 'context_read_unavailable'),
    ('input', 'input_changed'), ('selection', 'selection_changed'),
])
async def test_pretransport_diagnosis_is_exact_to_current_stage(runtime, monkeypatch, single, change, code):
    r = runtime;await r.read(single=single);request = r.request()
    fitted = await r.provider.request_budget(request)
    assert pixels(fitted) == (r.pair.data[:1] if single else r.pair.data)
    before = fitted.model_dump_json()
    if change == 'input':r.epoch[:] = ['later-input']
    elif change == 'selection':await r.read(r.pair.rows[::-1], single=single)
    else:
        original = r.pair.app.app_bridge
        async def bridge(operation, args, sid):
            if (operation.startswith('outputs.image') and change.startswith('read_') or
                    operation == 'context.manifest' and change.startswith('context_')):
                if change.endswith('timeout'):raise TimeoutError('injected deadline')
                raise ValueError('injected unavailable read')
            return await original(operation, args, sid)
        monkeypatch.setattr(r.pair.app, 'app_bridge', bridge)
    sent = await r.provider.complete(request)
    value, text = observation(sent)
    assert not pixels(sent) and reason(value, single) == code
    assert value['stage'] == 'pre_transport'
    assert value['toolCallId'] == 'image-call-1'
    assert fitted.model_dump_json() == before
    assert 'provider delivery acknowledgement' in text
    if not single:
        assert len(value['omitted']) == 2 and not value['comparisonReady']
        assert value['inspectionReceipt'] == observation(fitted)[0]['inspectionReceipt']


async def test_pair_missing_receipt_is_distinct_from_unavailable_pixels(runtime):
    r = runtime;await r.read()
    fitted = await r.provider.request_budget(r.request())
    without_receipt = fitted.model_copy(update={'messages': [message for message in fitted.messages
        if not (message.role == 'tool' and message.tool_call_id == 'image-call-1')]})
    sent = await r.delivery.revalidate(without_receipt)
    value, _ = observation(sent)
    assert not pixels(sent) and reason(value, False) == 'receipt_unavailable'


@pytest.mark.parametrize('single', [False, True])
async def test_unsupported_model_has_its_own_reason(runtime, single):
    r = runtime;await r.read(single=single);r.provider.selection['model'] = 'text'
    sent = await r.provider.complete(r.request())
    value, _ = observation(sent)
    assert not pixels(sent) and reason(value, single) == 'vision_unavailable'


async def test_cancelled_read_is_not_relabelled_as_an_image_failure(runtime, monkeypatch):
    r = runtime;await r.read()
    original = r.pair.app.app_bridge
    async def bridge(operation, args, sid):
        if operation.startswith('outputs.image'):raise asyncio.CancelledError()
        return await original(operation, args, sid)
    monkeypatch.setattr(r.pair.app, 'app_bridge', bridge)
    with pytest.raises(asyncio.CancelledError):
        await r.provider.complete(r.request())
