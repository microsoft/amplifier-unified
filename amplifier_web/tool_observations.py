"""Bounded inspection hints and exact event identities, never execution authority."""
from __future__ import annotations
import uuid

INPUT_KEYS = ('command', 'cmd', 'code', 'action', 'operation', 'file_path', 'path',
              'filename', 'instruction', 'instructions', 'prompt', 'task', 'agent',
              'name', 'description')


def bounded(value):
    return value if len(value) <= 180 else value[:179] + '…'


def input_summary(value):
    if not isinstance(value, dict):
        return {}
    return {key: bounded(value[key]) for key in INPUT_KEYS if isinstance(value.get(key), str)}


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

