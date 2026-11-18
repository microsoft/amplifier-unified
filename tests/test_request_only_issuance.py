"""Ephemeral preparation ownership is distinct from admitted upload inputs."""
import asyncio

import pytest
from amplifier_core.message_models import ChatRequest, Message

from amplifier_web.host.media import RequestOnlyRegistry, record_request_only, request_only_scope


def image_message():
    return Message(role="user", content=[{
        "type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "eA=="}}])


def valid(registry, request):
    return registry.validate(request, message_index=0, block_index=0, purpose="model")


@pytest.mark.parametrize("cross_task", [False, True])
@pytest.mark.parametrize("new_registry", [False, True])
async def test_request_only_release_prevents_issuance_reuse(cross_task, new_registry):
    registry = RequestOnlyRegistry()
    message = image_message()
    with request_only_scope() as issued:
        record_request_only(message, validate=lambda: True)
        first = ChatRequest(messages=[message])
        assert registry.bind(first, issued)
        assert valid(registry, first)
    # Bound lifetime survives the collection scope, until explicit release.
    assert valid(registry, first)
    registry.release(first)
    assert not valid(registry, first)
    target = RequestOnlyRegistry() if new_registry else registry
    async def rebind():
        second = ChatRequest(messages=[message])  # The *exact same* image block.
        with request_only_scope():
            assert not target.bind(second, issued)
        assert not valid(target, second)
    if cross_task:
        await asyncio.create_task(rebind())
    else:
        await rebind()
    assert not registry.requests and not target.requests


async def test_request_only_can_bind_only_once_even_inside_original_scope():
    registry, other = RequestOnlyRegistry(), RequestOnlyRegistry()
    message = image_message()
    with request_only_scope() as issued:
        record_request_only(message, validate=lambda: True)
        first, second = ChatRequest(messages=[message]), ChatRequest(messages=[message])
        assert registry.bind(first, issued)
        assert not registry.bind(second, issued)
        assert not other.bind(second, issued)
        # Releasing inside the collection scope cannot revive its issuance.
        registry.release(first)
        assert not registry.bind(second, issued)
        record_request_only(message, validate=lambda: True)
        assert not registry.bind(second, issued)


async def test_request_only_expired_unbound_scope_cannot_be_used_or_copied():
    registry = RequestOnlyRegistry()
    message = image_message()
    with request_only_scope() as issued:
        record_request_only(message, validate=lambda: True)
        copied_list = list(issued.blocks)
    request = ChatRequest(messages=[message])
    with request_only_scope():
        assert not registry.bind(request, issued)
        assert not registry.bind(request, copied_list)
    assert not valid(registry, request)
    assert not issued.blocks and issued.task is None


async def test_request_only_rejects_binding_and_validation_from_another_task():
    registry = RequestOnlyRegistry()
    message = image_message()
    validations = []
    def validate():
        validations.append(True)
        return True
    with request_only_scope() as issued:
        record_request_only(message, validate=validate)
        request = ChatRequest(messages=[message])
        async def foreign_bind():
            # ContextVars are inherited by child tasks; that is not ownership.
            record_request_only(message, validate=lambda: True)
            assert not registry.bind(request, issued)
        await asyncio.create_task(foreign_bind())
        assert registry.bind(request, issued)
    assert valid(registry, request)
    async def foreign_validate():
        assert not valid(registry, request)
    await asyncio.create_task(foreign_validate())
    assert validations == [True], "Foreign validation must not invoke the issuer's callback"
    registry.release(request)


async def test_request_only_finished_owner_cannot_transfer_a_bound_association():
    registry = RequestOnlyRegistry()
    async def owner():
        message = image_message()
        with request_only_scope() as issued:
            record_request_only(message, validate=lambda: True)
            request = ChatRequest(messages=[message])
            assert registry.bind(request, issued)
            assert valid(registry, request)
        return request, issued
    request, issued = await asyncio.create_task(owner())
    assert not valid(registry, request)
    registry.release(request)
    with request_only_scope():
        assert not registry.bind(request, issued)


async def test_request_only_nested_scope_cannot_consume_outer_issuance():
    registry = RequestOnlyRegistry()
    message = image_message()
    with request_only_scope() as outer:
        record_request_only(message, validate=lambda: True)
        request = ChatRequest(messages=[message])
        with request_only_scope():
            assert not registry.bind(request, outer)
        assert registry.bind(request, outer)
    assert valid(registry, request)
    registry.release(request)


async def test_request_only_fresh_preparation_is_required_after_release():
    registry = RequestOnlyRegistry()
    message = image_message()
    for _ in range(2):
        with request_only_scope() as fresh:
            record_request_only(message, validate=lambda: True)
            request = ChatRequest(messages=[message])
            assert registry.bind(request, fresh)
        assert valid(registry, request)
        registry.release(request)
        assert not valid(registry, request)


async def test_request_only_upload_issuance_remains_durable_and_task_independent(tmp_path):
    path = tmp_path / "upload-issuance.json"
    registry = RequestOnlyRegistry(path)
    message = image_message()
    content = [block.model_dump(exclude_none=True) for block in message.content]
    registry.issue_upload("admitted-input", content)
    message.metadata = {"amplifier_input": {"version": 1, "id": "admitted-input"}}
    request = ChatRequest(messages=[message])
    assert registry.bind(request)
    assert valid(registry, request)
    registry.release(request)
    reloaded = RequestOnlyRegistry(path)
    async def new_owner():
        assert reloaded.bind(request)
        assert valid(reloaded, request)
        reloaded.release(request)
    await asyncio.create_task(new_owner())
    assert not registry.requests and not reloaded.requests