"""Bounded display facts from host-owned recovery notices, never user text."""
import json
import re


def recovery_facts(metadata, text, provenance):
    if provenance.get('source') != 'local-job-recovery':
        return None
    value = metadata.get('recovery')
    if not isinstance(value, dict) or value.get('version') != 1:
        # Older loop-live persisted these facts inside its service observation.
        # The caller has already established host provenance from metadata.
        if len(text) > 16384:
            return None
        try:
            envelope = json.loads(text.split('\n', 1)[1])['observation']
            if envelope['source'] != 'local-job-recovery':
                return None
            value = json.loads(envelope['text'])
        except (ValueError, TypeError, KeyError, IndexError, RecursionError):
            return None
    if not isinstance(value, dict):
        return None
    job_id = value.get('job_id')
    identity = metadata.get('live_recovery_job') or provenance.get('id')
    if job_id != identity:
        return None
    result = {}
    for key in ('job_id', 'call_id'):
        item = value.get(key)
        if not isinstance(item, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,200}', item):
            return None
        result[key] = item
    if provenance.get('call_id') and provenance['call_id'] != result['call_id']:
        return None
    for key, choices in {
        'status': {'returned', 'cancelled', 'interrupted', 'failed', 'pending'},
        'outcome': {'tool_report_unverified', 'unconfirmed'},
        'reason': {'restored_evidence', 'changed_evidence', 'uncertain_outcome'},
    }.items():
        if isinstance(value.get(key), str) and value[key] in choices:
            result[key] = value[key]
    return result
