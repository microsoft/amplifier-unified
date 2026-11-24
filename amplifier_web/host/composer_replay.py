"""Fit the original user input for a provider using ordinary inline image blocks."""
import base64
import copy
import hashlib
import inspect
import os
from pathlib import Path

from .media import (NAMESPACE, RequestOnlyRegistry, WarningProjection, _effective_model,
                    _prepare_supported, record_request_only, request_only_scope)


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
    return await _inline_view(coordinator, provider, request, options, positions, subset, model, support)


async def _inline_view(coordinator, provider, request, options, positions, subset, model, support):
    registry = coordinator.get_capability('web.media.request_only')
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


def _file_identity(path):
    st = path.stat(follow_symlinks=False)
    return st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns


def _original_message(coordinator, store, home, message):
    from .. import attachments
    from ..composer_handoff import inline_content
    refs = message.metadata[NAMESPACE]['refs']
    files = []
    policy = store.policy

    def read(ref):
        origin = ref['provenance']
        if (origin['kind'] != 'upload' or origin['session_id'] != store.session_id
                or origin['expires_at'] is not None or store.get_origin_classification(ref) != 'composer-upload'):
            raise ValueError('The image does not belong to this composer input')
        store.validate_ref(ref)  # Current canonical checkpoint and model-read authority.
        path, row = attachments.file_path(home, origin['id'])
        identity = _file_identity(path)
        data = attachments._image_bytes(path, row, policy)
        if (row['mime'] != ref['mime'] or len(data) != ref['bytes']
                or hashlib.sha256(data).hexdigest() != ref['sha256'] or _file_identity(path) != identity):
            raise ValueError('The original attachment no longer matches the saved input')
        files.append((ref, identity))
        return {'type': 'base64', 'media_type': row['mime'], 'data': base64.b64encode(data).decode('ascii')}

    def current():
        if coordinator.get_capability('web.media.store') is not store or store.policy != policy:
            return False
        for ref, identity in files:
            store.validate_ref(ref)
            path, row = attachments.file_path(home, ref['provenance']['id'])
            if row['mime'] != ref['mime'] or _file_identity(path) != identity:
                return False
        return True

    content = [block.model_dump(exclude_none=True) for block in message.content]
    ordinary = type(message).model_validate({**message.model_dump(),
                    'metadata': {k: v for k, v in message.metadata.items() if k != NAMESPACE},
                    'content': inline_content(content, refs, read)})
    return record_request_only(ordinary, validate=current)


async def ordinary_upload_projection(coordinator, provider, request, options, model, indexed):
    """Preserve the old unmarked ABI only for a request composed solely of verified uploads.

    Indexed declarations govern the provider's private-carrier interface. The
    ordinary provider still decides which user inputs it accepts on its own ABI.
    No private asset is opened to work around an unsupported carrier.
    """
    positions = tuple(i for i, m in enumerate(request.messages) if NAMESPACE in (m.metadata or {}))
    if not positions or any(request.messages[i].role != 'user' for i in positions):
        return None
    accepted = {row['message_index'] for row in indexed['positions']}
    images = []
    for i in positions:
        carrier = request.messages[i].metadata[NAMESPACE]
        if type(carrier) is not dict or not isinstance(carrier.get('refs'), list):
            return None
        for ref in carrier['refs']:
            if not isinstance(ref, dict) or ref.get('mime') not in indexed['mime_types']:
                return None  # A declared MIME exclusion applies to that stored image.
            images.append((i, ref['mime']))
    blocks = [(i, j, b) for i, m in enumerate(request.messages) if isinstance(m.content, list)
              for j, b in enumerate(m.content) if b.type == 'image']
    images.extend((i, block.source.get('media_type')) for i, _, block in blocks)
    if all(i in accepted and mime in indexed['mime_types'] for i, mime in images):
        return None
    registry = coordinator.get_capability('web.media.request_only')
    if (positions != tuple(_issued_rows(request, registry))
            or any(request.messages[i].role != 'user'
                   or not registry.validate(request, message_index=i, block_index=j, purpose='model', upload_only=True)
                   for i, j, _ in blocks)):
        return None
    support = _ordinary_support(provider, request, model, options)
    if 'user' not in support['positions'] or ('mime_types' in support and
            any(mime not in support['mime_types'] for _, mime in images)):
        return None
    home = Path(os.environ.get('AMPLIFIER_WEB_HOME') or Path.home() / '.amplifier-unified')
    store = coordinator.get_capability('web.media.store')
    with request_only_scope() as issued:
        try:
            messages = [_original_message(coordinator, store, home, request.messages[i]) for i in positions]
        except (OSError, ValueError):
            # Before fit, a missing/changed upload or source refusal leaves the
            # existing indexed decision and its original availability behavior.
            return None
        subset = request.model_copy(update={'messages': messages})
        registry.bind(subset, issued)
    return await _inline_view(coordinator, provider, request, options, positions, subset, model, support)
