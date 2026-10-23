"""Finite provider-startup facts, never exception text or configuration values."""
from __future__ import annotations


STAGES = {'preparation', 'schema', 'configuration', 'mount'}
CATEGORIES = {'unknown', 'missing_dependency', 'import_failure', 'missing_environment',
              'missing_credential', 'configuration', 'schema', 'timeout', 'connection',
              'authentication', 'permission', 'rate_limit', 'service', 'invalid_request'}
ERROR_TYPES = {'Error', 'ValueError', 'TypeError', 'RuntimeError', 'OSError',
               'FileNotFoundError', 'PermissionError', 'ImportError', 'ModuleNotFoundError',
               'TimeoutError', 'ConnectionError', 'AuthenticationError', 'PermissionDeniedError',
               'RateLimitError', 'APIConnectionError', 'APITimeoutError', 'APIStatusError',
               'InternalServerError', 'BadRequestError', 'ValidationError', 'ProviderConfigurationError'}


class ProviderConfigurationError(ValueError):
    """Locally verified configuration cause; the original private message stays local."""
    def __init__(self, message, *, category):
        super().__init__(message)
        self.category = category


def safe_diagnostic(value):
    if (not isinstance(value, dict) or not isinstance(value.get('stage'), str)
            or value['stage'] not in STAGES):
        return None
    category = value.get('category')
    kind = value.get('errorType')
    result = {'stage': value['stage'],
              'category': category if isinstance(category, str) and category in CATEGORIES else 'unknown',
              'errorType': kind if isinstance(kind, str) and kind in ERROR_TYPES else 'Error'}
    status = value.get('httpStatus')
    if type(status) is int and 400 <= status <= 599:
        result['httpStatus'] = status
    return result


def diagnose(error, *, stage):
    """Classify observed types/status only, without parsing arbitrary error prose.

    A generic mount ValueError does not establish invalid credentials. Unknown
    failures retain their stage and known exception type for investigation.
    """
    category = 'unknown'
    kind = type(error).__name__ if isinstance(error, BaseException) else 'Error'
    if isinstance(error, ProviderConfigurationError):
        category = error.category
    elif isinstance(error, ModuleNotFoundError):
        category = 'missing_dependency'
    elif isinstance(error, ImportError):
        category = 'import_failure'
    elif isinstance(error, TimeoutError) or kind == 'APITimeoutError':
        category = 'timeout'
    elif isinstance(error, ConnectionError) or kind == 'APIConnectionError':
        category = 'connection'
    elif kind in {'AuthenticationError', 'PermissionDeniedError', 'RateLimitError',
                  'InternalServerError', 'BadRequestError'}:
        category = {'AuthenticationError': 'authentication', 'PermissionDeniedError': 'permission',
                    'RateLimitError': 'rate_limit', 'InternalServerError': 'service',
                    'BadRequestError': 'invalid_request'}[kind]
    elif stage in {'schema', 'configuration'} and isinstance(error, (ValueError, TypeError)):
        category = stage
    status = None
    try:
        observed = getattr(error, 'status_code', None)
        if type(observed) is int and 400 <= observed <= 599:
            status = observed
    except Exception:
        pass  # Diagnostic inspection cannot replace the provider failure.
    if status is not None:
        category = ('authentication' if status == 401 else 'permission' if status == 403 else
                    'rate_limit' if status == 429 else 'service' if status >= 500 else
                    'invalid_request' if status in {400, 422} else category)
    return safe_diagnostic({'stage': stage, 'category': category, 'errorType': kind,
                            **({'httpStatus': status} if status is not None else {})})


def guidance(diagnostic):
    """Useful finite remediation for a known observation, never a guessed cause."""
    category = diagnostic['category']
    if category == 'missing_environment':
        return 'A required environment reference is unavailable for this connection. Check its configured environment.'
    if category == 'missing_credential':
        return 'A required credential is missing from this connection’s configuration.'
    if category == 'missing_dependency':
        return 'A required Python module is unavailable while preparing this connection. Check its module source and dependencies.'
    if category == 'import_failure':
        return 'A provider dependency could not be imported. Check the provider module and its installed dependencies.'
    if category in {'authentication', 'permission', 'rate_limit', 'service'}:
        observed = {'authentication': 'An authentication rejection', 'permission': 'A permission rejection',
                    'rate_limit': 'A rate limit', 'service': 'A service error'}[category]
        return observed + ' occurred during provider ' + diagnostic['stage'] + '. Check this connection’s startup diagnostic.'
    return None
