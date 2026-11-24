"""Fit the original user input for a provider using ordinary inline image blocks."""
import copy
import hashlib
import inspect

from .media import (NAMESPACE, RequestOnlyRegistry, WarningProjection, _effective_model,
                    _prepare_supported)


def _issued_rows(request, registry):
    if type(registry) is not RequestOnlyRegistry:
        return
    for i, message in enumerate(request.messages):
        metadata = message.metadata or {}
        source = metadata.get('amplifier_input') or {}
        carrier = metadata.get(NAMESPACE)
        if (message.role != 'user' or not isinstance(message.content, list)
                or type(carrier) is not dict or set(carrier) != {'refs'}
                or source.get('kind') not in {'user', 'steer'} or source.get('source', 'user') != 'user'):
            continue
        content = [block.model_dump(exclude_none=True) for block in message.content]
        if registry.issued_composer_refs(source.get('id'), content, carrier['refs']):
            yield i


def _ordinary_support(provider, request, model, options):
    # The legacy composer sent ordinary inline user inputs. Preserve that format
    # for issuer-bound uploads only. Honor any explicit narrower provider policy.
    getter = getattr(provider, 'get_media_request_support', None)
    if getter is None:
        return {'v': 1, 'positions': ['user']}
    value = getter(model=model, request_options=copy.deepcopy(options))
    if inspect.iscoroutine(value):
        value.close()
    if (type(value) is not dict or type(value.get('v')) is not int or value['v'] != 1
            or type(value.get('positions')) is not list or 'user' not in value['positions']
            or any(type(role) is not str for role in value['positions'])
            or 'mime_types' in value and (type(value['mime_types']) is not list
                                          or any(type(mime) is not str for mime in value['mime_types']))):
        return {'v': 1, 'positions': []}
    return {'v': 1, 'positions': ['user'],
            **({'mime_types': list(value['mime_types'])} if 'mime_types' in value else {})}


def _fingerprint(request):
    return hashlib.sha256(request.model_dump_json().encode()).digest()


class _InlineComposerView(WarningProjection):
    def validate(self):
        super().validate()
        current = self.coordinator.get_capability('web.media.request_only')
        if current is not self.registry or tuple(_issued_rows(self.source, current)) != self.positions:
            raise ValueError('The original composer admission changed')
        self.pixels.validate()
        # The exposed blocks share their identity with the Foundation candidate.
        # Refuse swapped sequence/metadata in the unmarked legacy request, too.
        if _fingerprint(self.request) != self.expected:
            raise ValueError('Fitted composer inputs changed before delivery')

    def release(self):
        try:
            self.pixels.release()
        finally:
            self.expected = None
            super().release()


async def legacy_projection(coordinator, provider, request, options):
    registry = coordinator.get_capability('web.media.request_only')
    positions = tuple(_issued_rows(request, registry))
    if not positions:
        return WarningProjection(request, options, coordinator=coordinator)
    model = _effective_model(provider, request, options)
    # Only issued user rows reach the model-read boundary. Other references use
    # the existing unmarked warning, including mixed histories with tool results.
    subset = request.model_copy(update={'messages': [request.messages[i] for i in positions]})
    support = _ordinary_support(provider, subset, model, options)
    if not support['positions']:
        return WarningProjection(request, options, coordinator=coordinator)
    registry.bind(subset)
    pixels = await _prepare_supported(coordinator, provider, subset, options, model, support, _ordinary_support)
    try:
        view = _InlineComposerView(request, options, coordinator=coordinator)
        view.registry, view.positions, view.pixels, view.provider = registry, positions, pixels, provider
        for i, message in zip(positions, pixels.request.messages, strict=True):
            view.request.messages[i].content = message.content
        view.expected = _fingerprint(view.request)
        return view
    except BaseException:
        pixels.release()
        raise
