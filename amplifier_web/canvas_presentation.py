"""Explicit tool-owned dashboard identity without widening saved authority."""
from .mcp_view_recovery import contract, digest
from .state_storage import resource


def identity(result):
    metadata = result.get('_meta') if isinstance(result, dict) else None
    value = metadata.get('amplifier/presentationId') if isinstance(metadata, dict) else None
    return value if isinstance(value, str) and 0 < len(value) <= 200 else None


def key(binding, presentation_id, operation_id):
    namespace = [binding['configuration'], binding.get('accountIdentity')]
    if presentation_id:
        # A tool may use several methods to present the same durable entity.
        return digest([*namespace, binding['resourceUri'], ['presentation', presentation_id]])
    return digest([*namespace, binding['tool'], binding['resourceUri'], ['operation', operation_id]])


def compatible(service, old, new):
    if not isinstance(old, dict) or not isinstance(new, dict):
        return False
    server = next((row for row in service.state['smartTools']['servers'] if row['id'] == new['serverId']), None)
    if not server or server.get('account', {}).get('status') in {'unconfirmed', 'changed', 'accepted'}:
        return False
    for field in ('serverId', 'configuration', 'accountIdentity', 'resourceUri', 'requestedCsp', 'requestedPermissions'):
        if old.get(field) != new.get(field):
            return False
    if set(old.get('allowedTools', [])) != set(new.get('allowedTools', [])):
        return False
    before, after = contract(service, old), contract(service, new)
    # A matching name is insufficient: the old saved grants must still match
    # today's schemas. Missing legacy authority cannot be inferred from a title.
    return bool(before and after and old.get('contractFingerprint') == before['fingerprint']
                and new.get('contractFingerprint') == after['fingerprint']
                and before['tools'] == after['tools'])


def previous(service, canvas, presentation_id):
    for row in service.state.get('canvasArtifacts', []):
        if row.get('kind') != 'mcp-app' or any(row.get(field) != canvas.get(field) for field in ('sessionId', 'workspaceId')):
            continue
        if not presentation_id:
            if row.get('presentationKey') == canvas['presentationKey']:
                return row
            continue
        try:
            binding = resource(service.db, row['mcpState']['$resource'])
            if not compatible(service, binding, canvas['mcp']):
                continue
            saved = resource(service.db, binding['savedResult']['$resource'])
            if identity(saved) == presentation_id:
                # This also recognizes the previous launcher-scoped key. Keep
                # its artifact ID and every exact-version link when upgrading.
                return row
        except (KeyError, ValueError, TypeError, OSError):
            continue  # Keep damaged artifacts; never replace their references.
    return None
