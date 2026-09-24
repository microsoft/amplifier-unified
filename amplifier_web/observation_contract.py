"""Strict, provider-independent observation envelopes and qualification checks."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import jsonschema

from amplifier_scheduling.store import fingerprint
from .smart_tools import configuration_key

CONTRACT = 'amplifier.observation.v1'


def bounded(value, maximum=32000):
    if len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode()) > maximum:
        raise ValueError('Observation data exceeds its bounded contract')
    return copy.deepcopy(value)


def text(value, name, maximum=2000):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f'Invalid observation {name}')
    return value


def local_schema(spec):
    bounded(spec)
    seen = set()
    maps = {'properties', 'patternProperties', '$defs', 'definitions', 'dependentSchemas'}
    singles = {'additionalProperties', 'unevaluatedProperties', 'propertyNames', 'items', 'contains',
               'unevaluatedItems', 'not', 'if', 'then', 'else', 'contentSchema'}
    lists = {'allOf', 'anyOf', 'oneOf', 'prefixItems'}
    def walk(value, depth=0):
        if depth > 24: raise ValueError('Observation schema is nested too deeply')
        if not isinstance(value, dict) or id(value) in seen: return
        seen.add(id(value))
        if any(key in value for key in ('$id', 'id', '$dynamicRef', '$recursiveRef', '$recursiveAnchor', '$dynamicAnchor')):
            raise ValueError('Observation schemas cannot declare identities or dynamic references')
        if '$schema' in value and value['$schema'] != 'https://json-schema.org/draft/2020-12/schema':
            raise ValueError('Observation schemas support only JSON Schema 2020-12')
        if '$ref' in value:
            ref = value['$ref']
            if not isinstance(ref, str) or not ref.startswith('#/'):
                raise ValueError('Observation schemas allow only local references')
            target = spec
            try:
                from urllib.parse import unquote
                for part in unquote(ref[2:]).split('/'):
                    key = part.replace('~1', '/').replace('~0', '~')
                    target = target[int(key)] if isinstance(target, list) else target[key]
            except (KeyError, IndexError, ValueError, TypeError) as exc:
                raise ValueError('Observation schema reference has no local target') from exc
            walk(target, depth + 1)
        for key, item in value.items():
            if key in maps and isinstance(item, dict):
                for child in item.values(): walk(child, depth + 1)
            elif key in singles: walk(item, depth + 1)
            elif key in lists and isinstance(item, list):
                for child in item: walk(child, depth + 1)
        # const/default/examples and property names are data, not schema keywords.
    walk(spec)
    jsonschema.Draft202012Validator.check_schema(spec)


def validate_schema(value, spec):
    from referencing import Registry
    from referencing.exceptions import NoSuchResource
    def no_retrieval(uri):
        raise NoSuchResource(ref=uri)
    local_schema(spec)
    jsonschema.Draft202012Validator(spec, registry=Registry(retrieve=no_retrieval)).validate(value)


def validate_result(value, watch):
    bounded(value)
    if not isinstance(value, dict) or value.get('contract') != CONTRACT:
        raise ValueError('The observer returned an unsupported result contract')
    if set(value) - {'contract', 'status', 'target', 'source', 'observedAt', 'semanticKey', 'summary', 'evidence', 'cursor', 'presentation'}:
        raise ValueError('The observer returned undeclared result fields')
    status = value.get('status')
    if status not in {'pending', 'actionable', 'observation_failed'}:
        raise ValueError('The observer did not establish a supported recorded state')
    if value.get('target') != watch['target']:
        raise ValueError('The observation belongs to a different target')
    source = value.get('source')
    if not isinstance(source, dict) or set(source) != {'id', 'revision'} or source['id'] != watch['sourceId']:
        raise ValueError('The observation belongs to a different source')
    for item, name in ((source['revision'], 'source revision'), (value.get('semanticKey'), 'semantic key')):
        if item is not None or status != 'observation_failed': text(item, name, 500)
    observed = value.get('observedAt')
    if isinstance(observed, bool) or not isinstance(observed, (int, float)) or observed < 0:
        raise ValueError('Invalid observation time')
    text(value.get('summary'), 'summary', 4000)
    evidence = value.get('evidence')
    if not isinstance(evidence, list) or len(evidence) > 16 or (status == 'actionable' and not evidence):
        raise ValueError('An actionable observation requires bounded exact evidence')
    for row in evidence:
        if not isinstance(row, dict) or set(row) != {'uri', 'revision', 'digest'}:
            raise ValueError('Invalid immutable observation evidence')
        for key in row: text(row[key], 'evidence ' + key, 2000 if key == 'uri' else 500)
    from .observation_presentation import candidate
    candidate(value.get('presentation'), value)
    bounded(value.get('cursor'), 4000)
    return copy.deepcopy(value)


def account_binding(server):
    if server.get('transport', 'stdio') == 'stdio':
        return {'mode': 'trusted-local', 'bindingHash': fingerprint({'configuration': configuration_key(server), 'installation': server.get('installationId')})}
    if server.get('account', {}).get('status') != 'verified' or not server.get('accountBinding'):
        raise ValueError('Remote observation requires a currently verified connector account')
    return {'mode': 'attested', 'bindingHash': fingerprint(server['accountBinding']), 'revision': server.get('accountRevision'), 'grantedScopes': sorted(server.get('authorization', {}).get('grantedScopes') or [])}


def artifacts(manager, descriptor):
    # Exact reviewed files, not arbitrary paths or a claim made by MCP metadata.
    root = (manager.root / 'installs' / descriptor['installationId']).resolve()
    for entry in descriptor['artifacts']:
        relative = Path(entry['path'])
        candidate = root / relative
        if relative.is_absolute() or '..' in relative.parts or not candidate.resolve().is_relative_to(root):
            raise ValueError('Qualified observation code escaped its installation')
        if not candidate.is_file() or hashlib.sha256(candidate.read_bytes()).hexdigest() != entry['sha256']:
            raise ValueError('Qualified observation implementation changed or is missing')


def qualify(manager, supplied):
    descriptor = bounded(supplied)
    required = {'installationId', 'connectionId', 'toolName', 'implementationRevision', 'requestArgument', 'targetSchema', 'scopeSchema', 'artifacts', 'qualification'}
    if not isinstance(descriptor, dict) or set(descriptor) != required:
        raise ValueError('Supply the exact qualified observation descriptor fields')
    for key in ('installationId', 'connectionId', 'toolName', 'implementationRevision', 'requestArgument'): text(descriptor[key], key, 200)
    for key in ('targetSchema', 'scopeSchema'):
        if not isinstance(descriptor[key], dict): raise ValueError('Observation scope schemas must be objects')
        local_schema(descriptor[key])
    proof = descriptor['qualification']
    if not isinstance(proof, dict) or set(proof) != {'kind', 'evidence', 'providerFree', 'concurrentReadSafe'}:
        raise ValueError('Source review evidence is required; readOnlyHint is insufficient')
    if proof['kind'] not in {'source-enforced-read', 'reviewed-trusted-read'} or proof['providerFree'] is not True or proof['concurrentReadSafe'] is not True:
        raise ValueError('Only qualified provider-free concurrent reads are eligible')
    if not isinstance(proof['evidence'], list) or not 1 <= len(proof['evidence']) <= 8: raise ValueError('Source qualification evidence is required')
    for row in proof['evidence']:
        if not isinstance(row, dict) or set(row) != {'uri', 'digest'}: raise ValueError('Invalid qualification evidence')
        text(row['uri'], 'review URI'); text(row['digest'], 'review digest', 200)
    if not isinstance(descriptor['artifacts'], list) or not 1 <= len(descriptor['artifacts']) <= 100: raise ValueError('Reviewed installed-file digests are required')
    for row in descriptor['artifacts']:
        if not isinstance(row, dict) or set(row) != {'path', 'sha256'}: raise ValueError('Invalid implementation digest')
        text(row['path'], 'file'); text(row['sha256'], 'file digest', 64)
    installation = next((row for row in manager.state['installations'] if row['id'] == descriptor['installationId'] and row.get('status') == 'installed'), None)
    server = manager._server(descriptor['connectionId'])
    if not installation or server.get('installationId') != descriptor['installationId']:
        raise ValueError('Choose the exact installed observation connection')
    if server.get('status') != 'connected' or server.get('connectionState') != 'ready':
        raise ValueError('Connect and qualify the observer explicitly before use')
    tool = next((row for row in manager._catalog(server['id']) if row.get('name') == descriptor['toolName']), None)
    if not tool or not tool.get('outputSchema'): raise ValueError('The observer must advertise both input and output schemas')
    local_schema(tool.get('inputSchema', {})); local_schema(tool['outputSchema'])
    descriptor.update(contract=CONTRACT, configurationKey=configuration_key(server),
        installationCommit=installation.get('commit'), inputSchemaHash=fingerprint(tool.get('inputSchema')), outputSchemaHash=fingerprint(tool['outputSchema']),
        account=account_binding(server), connectionPolicy='existing-only')
    artifacts(manager, descriptor)
    descriptor['id'] = fingerprint(descriptor)
    return descriptor


def check(manager, descriptor):
    server = manager._server(descriptor['connectionId'])
    if configuration_key(server) != descriptor['configurationKey'] or server.get('installationId') != descriptor['installationId']:
        raise ValueError('The observation connection or installation changed')
    installation = next((row for row in manager.state['installations'] if row['id'] == descriptor['installationId'] and row.get('status') == 'installed'), None)
    if not installation or installation.get('commit') != descriptor['installationCommit']:
        raise ValueError('The qualified observation source changed')
    if server.get('status') != 'connected' or server.get('connectionState') != 'ready':
        raise ValueError('The qualified observation registration is not ready')
    if server['id'] not in manager.connections or manager.connections[server['id']].task.done():
        raise ValueError('The qualified observation connection is not available; no reconnect was attempted')
    if account_binding(server) != descriptor['account']: raise ValueError('The qualified observation account changed')
    tool = next((row for row in manager._catalog(server['id']) if row.get('name') == descriptor['toolName']), None)
    if not tool or fingerprint(tool.get('inputSchema')) != descriptor['inputSchemaHash'] or fingerprint(tool.get('outputSchema')) != descriptor['outputSchemaHash']:
        raise ValueError('The qualified observation contract changed')
    artifacts(manager, descriptor)
    return tool
