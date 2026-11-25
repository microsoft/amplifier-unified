"""Reference composition identity. Packages contain deltas, never a copied base."""
import hashlib
import json

BASE = 'reference-v1'
REFERENCE_ID = 'reference'


def foundation(service):
    build = service.installed_build.project()
    return {'id': BASE, 'build': build, 'available': build['frontend']['id'] != 'unknown'}


def composition(service, theme, source, effective, preview_id=None):
    value = {'foundation': foundation(service),
             'package': {'id': theme['id'], 'fingerprint': theme['fingerprint']},
             'preferences': {'source': source, 'values': effective}, 'previewId': preview_id}
    return {**value, 'id': hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()}
