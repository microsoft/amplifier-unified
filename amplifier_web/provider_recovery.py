"""Read the failed mount's connection choices without reopening its worker."""
import json
from pathlib import Path
from jsonschema import ValidationError


def catalog(home, session):
    if session.get('status') != 'error' or (session.get('failure') or {}).get('category') not in {
            'worker_startup', 'provider_selection'}:
        return None
    identity = session.get('runtimeSessionId') or session.get('nativeIdentity') or session.get('id')
    if not isinstance(identity, str) or Path(identity).name != identity or identity in {'.', '..'}:
        return None
    def read(path):
        if path.is_symlink() or path.stat().st_size > 2_000_000:
            raise ValueError('Invalid saved connection report')
        return json.loads(path.read_text())
    try:
        control = Path(home) / 'sessions' / identity / 'control-state.json'
        saved = read(control) if control.exists() else {}
        directory = Path(home) / 'runtime-reports' / identity
        choices = directory / 'provider-choices.json'
        report = read(choices if choices.exists() else directory / 'mounted.json')
        if not isinstance(saved, dict) or not isinstance(report, dict):
            return None
        from .new_chat import selection
        wanted = selection(report.get('selection') or saved.get('selection') or session.get('selection') or {})
        if not wanted or report.get('session_id') != identity or report.get('workspace') != session.get('workspace'):
            return None
        if report.get('module_load_failures'):
            return None
        choices = report.get('provider_choices', [])
        if not isinstance(choices, list):
            return None
        providers = []
        for row in choices[:200]:
            if not isinstance(row, dict) or not isinstance(row.get('id'), str):
                continue
            def label(key, limit=500):
                value = row.get(key)
                return value[:limit] if isinstance(value, str) else ''
            providers.append({'id': label('id', 200), 'info': {
                'id': label('provider', 200), 'display_name': label('display_name', 200),
                'defaults': {'model': label('model'), 'reasoning_effort': label('effort', 100)}}})
        if wanted['instance'] in {row['id'] for row in providers}:
            return None
        return {'providers': providers, 'selection': wanted, 'effective': wanted, 'pinned': True,
                'selectionIssue': 'The saved connection is no longer available. Choose a replacement; your model and reasoning choice are kept until you change them. This does not send your message.'}
    except (OSError, ValueError, TypeError, KeyError, ValidationError):
        return None
