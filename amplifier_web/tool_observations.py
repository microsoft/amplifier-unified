"""Bounded inspection hints and exact event identities, never execution authority."""
from __future__ import annotations
import json
import math
import re
import uuid
from itertools import islice

INPUT_KEYS = ('command', 'cmd', 'code', 'action', 'operation', 'file_path', 'path',
              'filename', 'instruction', 'instructions', 'prompt', 'task', 'agent',
              'name', 'description')


def bounded(value):
    return value if len(value) <= 180 else value[:179] + '…'


def input_summary(value):
    if not isinstance(value, dict):
        return {}
    return {key: bounded(value[key]) for key in INPUT_KEYS if isinstance(value.get(key), str)}


# Bound both work and output: no full serialization/sort or arbitrary __str__.
PREVIEW_ITEMS = 4
PREVIEW_NODES = 24
PREVIEW_DEPTH = 2
_SECRET_FRAGMENTS = ('password', 'passwd', 'secret', 'apikey', 'privatekey',
                     'signingkey', 'accesskey', 'credential', 'authorization', 'cookie')


def _secret_key(key):
    if not isinstance(key, str) or len(key) > 96:
        return True
    normalized = re.sub(r'[^a-z0-9]', '', key.lower())
    return normalized.endswith('token') or any(part in normalized for part in _SECRET_FRAGMENTS)


def input_preview(value):
    """A small JSON-shaped hint, never complete input or a copy source.

    Insertion order is the recorded input order. Credential-named fields are
    redacted before traversal; other strings are not scanned for embedded secrets.
    """
    remaining = PREVIEW_NODES

    def short(text, limit):
        # Slice before normalizing whitespace so megabyte strings stay cheap.
        piece = text[:limit]
        piece = re.sub(r'\s+', ' ', piece)
        return piece + ('…' if len(text) > limit else '')

    def render(item, depth=0):
        nonlocal remaining
        if remaining <= 0:
            return '…'
        remaining -= 1
        if item is None:
            return 'null'
        if type(item) is bool:
            return 'true' if item else 'false'
        if isinstance(item, str):
            return json.dumps(short(item, 64), ensure_ascii=False)
        if type(item) is int:
            return str(item) if item.bit_length() <= 256 else '…'
        if type(item) is float:
            return json.dumps(item) if math.isfinite(item) else '…'
        if isinstance(item, dict):
            if not item:
                return '{}'
            if depth >= PREVIEW_DEPTH:
                return '{…}'
            parts = []
            for key, child in islice(item.items(), PREVIEW_ITEMS):
                label = short(key, 40) if isinstance(key, str) else '?'
                content = '"[redacted]"' if _secret_key(key) else render(child, depth + 1)
                parts.append(label + ('=' if depth == 0 else ': ') + content)
            if len(item) > PREVIEW_ITEMS:
                parts.append('…')
            joined = ', '.join(parts)
            return joined if depth == 0 else '{' + joined + '}'
        if isinstance(item, (list, tuple)):
            if not item:
                return '[]'
            if depth >= PREVIEW_DEPTH:
                return '[…]'
            parts = [render(child, depth + 1) for child in islice(item, PREVIEW_ITEMS)]
            if len(item) > PREVIEW_ITEMS:
                parts.append('…')
            return '[' + ', '.join(parts) + ']'
        return '…'

    return bounded(render(value))


def compact_image_tool(*names):
    return any(isinstance(name, str) and name in {'image_generate', 'nano-banana'} for name in names)


def input_hints(tool, value, *, present=True):
    # Image lifecycle observations admit status metadata, not prompts or paths.
    # Original event fields remain available through explicit saved inspection.
    if compact_image_tool(tool):
        return {'inputSummary': {}, 'inputPreview': '', 'actionPath': ''}
    result = {'inputSummary': input_summary(value),
              'inputPreview': input_preview(value) if present else '', 'actionPath': ''}
    if tool == 'app_control' and isinstance(value, dict) and value.get('operation') == 'dispatch':
        args = value.get('args')
        action = args.get('action') if isinstance(args, dict) else None
        if isinstance(action, str) and action:
            # This is a display path, not flattened/reconstructed arguments.
            result['actionPath'] = bounded('dispatch → ' + re.sub(r'\s+', ' ', bounded(action)))
    return result


def identity(value):
    if not isinstance(value, str):
        return None
    try:
        return value if str(uuid.UUID(value)) == value else None
    except ValueError:
        return None


def metadata(event):
    if event.get('kind') != 'tool':
        return {}
    result = {}
    tool = event.get('tool')
    if isinstance(tool, str) and 0 < len(tool) <= 160:
        result['tool'] = tool
    token = identity(event.get('toolObservationId'))
    if token:
        result['toolObservationId'] = token
    if compact_image_tool(tool, event.get('label')):
        # Also clear supplied/previous hints at each normalization boundary.
        return {**result, **input_hints(tool if compact_image_tool(tool) else event['label'], None),
                'outcomeSummary': {}}
    for key in ('inputPreview', 'actionPath'):
        if isinstance(event.get(key), str):
            result[key] = bounded(event[key])
    for key in ('inputSummary', 'outcomeSummary'):
        if key not in event:
            continue
        value = event[key]
        if key == 'inputSummary':
            result[key] = input_summary(value)
        else:
            summary = {}
            if isinstance(value, dict):
                if type(value.get('exitCode')) is int:
                    summary['exitCode'] = value['exitCode']
                if isinstance(value.get('error'), str):
                    summary['error'] = bounded(value['error'])
            result[key] = summary
    return result


def outcome_summary(name, data):
    """Small terminal hints; never substitute them for the saved result."""
    if name not in {'tool:post', 'tool:error'}:
        return {}
    result = data.get('result', data.get('tool_result'))
    result = result if isinstance(result, dict) else {}
    output = result.get('output')
    output = output if isinstance(output, dict) else result
    code = next((output[key] for key in ('returncode', 'exit_code', 'exitCode')
                 if type(output.get(key)) is int), None)
    errors = [data.get('error'), data.get('error_message'), result.get('error'), output.get('error')]
    errors = [value.get('message', value.get('msg')) if isinstance(value, dict) else value for value in errors]
    error = next((value for value in errors if isinstance(value, str) and value.strip()), None)
    failed = (name == 'tool:error' or result.get('success') is False
              or output.get('success') is False or code is not None and code != 0 or bool(error))
    if failed and error is None:
        error = next((value for value in (result.get('message'), output.get('message'))
                      if isinstance(value, str) and value.strip()), None)
        if error is None and isinstance(output.get('stderr'), str):
            error = output['stderr'].rstrip().rsplit('\n', 1)[-1]
    summary = {'exitCode': code} if code is not None else {}
    if error:
        summary['error'] = error if len(error) <= 180 else error[:179] + '…'
    return summary

