"""Content-free reasons at the local, before-transport fitting boundary."""
import json

COMPONENTS = ('messages', 'tools', 'tool_choice', 'model', 'max_output_tokens',
              'temperature', 'response_format', 'metadata')


class RequestFitChanged(ValueError):
    """A local refusal, not evidence that a provider request was sent."""

    def __init__(self, reason, *, components=()):
        if reason not in {'binding_missing', 'binding_changed', 'content_changed',
                          'authority_changed', 'authority_unavailable', 'native_scope_changed'}:
            raise ValueError('Unknown request fitting reason')
        if any(name not in (*COMPONENTS, 'other', 'unavailable') for name in components):
            raise ValueError('Unknown request fitting component')
        self.reason = reason
        self.components = tuple(components)
        detail = f" ({', '.join(self.components)})" if self.components else ''
        super().__init__(f'Work request refused before transport: {reason}{detail}; refit before transport.')


def changed_components(before, after):
    """Compare only on refusal; never retain or log prompt/image contents."""
    try:
        left, right = json.loads(before), json.loads(after)
        names = COMPONENTS
        changed = [name for name in names if left.get(name) != right.get(name)]
        if any(left.get(name) != right.get(name) for name in left.keys() | right.keys()
               if name not in names):
            changed.append('other')
        return tuple(changed)
    except (TypeError, ValueError, AttributeError):
        return ('unavailable',)
