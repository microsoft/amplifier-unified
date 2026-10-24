"""Fixed, content-free facts captured before a projection worker is retired."""
from pathlib import Path
import sys

STAGES = frozenset({'spool_setup', 'setup', 'setup_policy', 'setup_import', 'setup_store', 'input_decode', 'model', 'model_cache_load',
    'source_scan', 'source_bindings', 'source_rows', 'source_associate',
    'source_join', 'source_plan', 'source_usage', 'source_retain',
    'model_store', 'model_retain', 'output_encode'})
EXCEPTIONS = frozenset({'MemoryError', 'MemoryLimit', 'OSError', 'PermissionError',
    'FileNotFoundError', 'FileExistsError', 'IsADirectoryError', 'NotADirectoryError',
    'ValueError', 'ImportError', 'ModuleNotFoundError', 'RuntimeError', 'other'})
FIELDS = ('residentBytes', 'peakResidentBytes', 'virtualBytes',
          'peakVirtualBytes', 'addressSpaceLimitBytes')


def sanitize_failure(value):
    if (not isinstance(value, dict) or not isinstance(value.get('stage'), str)
            or value['stage'] not in STAGES):
        return None
    exception = value.get('exceptionType')
    return {'stage': value['stage'],
        'exceptionType': exception if isinstance(exception, str) and exception in EXCEPTIONS else None,
        'errno': value.get('errno') if type(value.get('errno')) is int and 0 <= value['errno'] <= 2**31 - 1 else None,
        **{key: value.get(key)
        if type(value.get(key)) is int and 0 <= value[key] <= 2**63 - 1 else None
        for key in FIELDS}}


def capture_failure(stage, exception=None):
    """Sample only on failure; missing platform facts stay explicitly unknown.

    Linux reports current/peak resident and virtual bytes. Other Unix systems
    provide peak RSS and the actual address-space soft limit, not current RSS.
    A failed allocation can also prevent measurement; the stage still survives.
    """
    kind = type(exception).__name__ if exception is not None else None
    result = {'stage': stage, 'exceptionType': kind if kind in EXCEPTIONS else 'other' if kind else None,
              'errno': exception.errno if isinstance(exception, OSError) else None, **dict.fromkeys(FIELDS)}
    try:
        import resource
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        result['peakResidentBytes'] = int(peak * (1 if sys.platform == 'darwin' else 1024))
        limit, _ = resource.getrlimit(resource.RLIMIT_AS)
        if limit != resource.RLIM_INFINITY:
            result['addressSpaceLimitBytes'] = limit
        if sys.platform.startswith('linux'):
            names = {'VmRSS:': 'residentBytes', 'VmHWM:': 'peakResidentBytes',
                     'VmSize:': 'virtualBytes', 'VmPeak:': 'peakVirtualBytes'}
            with Path('/proc/self/status').open() as stream:
                for line in stream.read(16384).splitlines():
                    parts = line.split()
                    if len(parts) == 3 and parts[0] in names and parts[2] == 'kB':
                        result[names[parts[0]]] = int(parts[1]) * 1024
    except (ImportError, AttributeError, OSError, ValueError, MemoryError):
        pass
    return sanitize_failure(result)
