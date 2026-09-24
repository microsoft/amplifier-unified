"""Recover a saved MCP view without replacing its source or replaying calls."""
import copy
import hashlib
import json

from .smart_tools import _visible, configuration_key
from .state_storage import resource


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def account(server):
    value = server.get('accountBinding')
    return {key: value.get(key) for key in ('issuer', 'subject')} if value else None


def contract(service, binding):
    """Hash the saved grants, not a random discovery generation or new grants."""
    tools = {tool['name']: tool for tool in getattr(service.smart_tools, 'schemas', {}).get(binding['serverId'], [])}
    launcher = tools.get(binding['tool'])
    names = sorted(set(binding['allowedTools']))
    if not launcher or launcher.get('_meta', {}).get('ui', {}).get('resourceUri') != binding['resourceUri']:
        return None
    if any(name not in tools or not _visible(tools[name], 'app') for name in names):
        return None
    value = {'launcher': launcher, 'tools': [tools[name] for name in names]}
    return {'fingerprint': digest(value), 'tools': copy.deepcopy(value['tools'])}


def revision(binding):
    return digest({key: binding.get(key) for key in (
        'serverId', 'configuration', 'catalogRevision', 'resourceUri', 'tool',
        'allowedTools', 'contractFingerprint', 'accountIdentity')})


def saved(service, identity, *, require_binding=True):
    from .service import AppError
    from .canvas_library import scope
    canvas = service.state.get('canvas', {})
    if canvas.get('id') != identity or canvas.get('kind') != 'mcp-app' or not canvas.get('open') or not scope(service.state, canvas):
        raise AppError('This tool view is no longer active in this chat. Reopen its canvas tab.', 409)
    row = next((row for row in service.state.get('canvasArtifacts', [])
                if row['id'] == identity and scope(service.state, row)), None)
    if not row:
        raise AppError('This saved tool document is unavailable in this chat.', 404)
    if require_binding and (not isinstance(canvas.get('mcp'), dict) or not canvas['mcp']):
        raise AppError('The saved tool binding is unavailable. Its source has not been replaced.', 409)
    return canvas, row


def source(service, identity):
    from .service import AppError
    canvas, row = saved(service, identity, require_binding=False)
    from .canvas_versions import definition
    row = definition(row, canvas.get('selectedVersion'))
    try:
        content = resource(service.db, row['body']['$resource'])['content']
        if not isinstance(content, str):
            raise ValueError('Invalid saved HTML')
        return content
    except (KeyError, ValueError, TypeError, OSError):
        raise AppError('The saved tool document is unavailable. Its original reference is retained; no work was replayed.',
                       404, code='canvas_source_unavailable') from None


def inspect(service, identity):
    from .service import AppError
    canvas, _ = saved(service, identity, require_binding=False)
    binding = canvas.get('mcp') if isinstance(canvas.get('mcp'), dict) else {}
    result = {'canvasId': identity, 'bindingRevision': revision(binding), 'source': 'available', 'canReconnect': False}
    def state(code, message, reconnect=False):
        return {**result, 'status': code, 'message': message, 'canReconnect': reconnect}
    try:
        source(service, identity)
    except AppError as error:
        result['source'] = 'unavailable'
        return state('source_unavailable', str(error))
    if canvas.get('readOnlyVersion'):
        return state('saved_version', 'Saved version, read only. Select Latest for live features; previous calls will not be replayed.')
    if not binding:
        return state('binding_unavailable', 'The saved tool connection details are unavailable. Its document is retained; open a new tool view to review a connection.')
    server = next((row for row in service.state['smartTools']['servers'] if row['id'] == binding['serverId']), None)
    if not server or configuration_key(server) != binding['configuration']:
        return state('configuration_changed', 'This server configuration changed. The saved document is retained; review the connection in Settings and open a new tool view.')
    if server.get('account', {}).get('status') in {'changed', 'accepted'} or (
            'accountIdentity' in binding and binding['accountIdentity'] != account(server)):
        return state('account_review_required', 'The saved view’s account cannot be confirmed. Review the account in Settings before using a tool view.')
    if server.get('status') not in {None, 'connected'} or server.get('catalogState') != 'current':
        return state('disconnected', 'Saved document available. Reconnect this tool to use its live features. Previous calls will not be replayed.', True)
    if server.get('account', {}).get('status') == 'unconfirmed':
        return state('account_review_required', 'The saved view’s account cannot be confirmed. Review the account in Settings before using a tool view.')
    current = contract(service, binding)
    if not current:
        return state('contract_changed', 'This tool’s available actions or app resource changed. The saved document is retained; open a new tool view to review the current contract.')
    if not binding.get('contractFingerprint'):
        # Legacy views did not record schemas or account identity. Never silently
        # convert a generation mismatch into approval of today's authority.
        if account(server):
            return state('account_review_required', 'This older view has no saved account identity. Review the account in Settings and open a new tool view.')
        return {**state('review_required', 'This older view has no saved tool contract. Review its existing actions before reconnecting this tab.', True),
                'review': current}
    if binding['contractFingerprint'] != current['fingerprint']:
        return state('contract_changed', 'This tool’s action schemas changed. The saved document is retained; open a new tool view to review the current contract.')
    if binding.get('catalogRevision') != server.get('catalogRevision'):
        return state('reconnect_required', 'Saved document available. Reconnect this tab to the verified current tool contract. Previous calls will not be replayed.', True)
    return state('ready', 'Tool view connected', True)


def admit(service, args):
    from .service import AppError
    status = inspect(service, args['canvasId'])
    if status['bindingRevision'] != args['expectedBindingRevision']:
        raise AppError('This tool view changed. Inspect it before reconnecting.', 409)
    if not status['canReconnect']:
        raise AppError(status['message'], 409, code=status['status'])
    return status


async def reconnect(service, args):
    from .service import AppError
    from .canvas_library import remember
    async with service.lock:
        admit(service, args)
        canvas, _ = saved(service, args['canvasId'])
        server_id = canvas['mcp']['serverId']
        configuration = canvas['mcp']['configuration']
    # connect retains an already healthy transport. Never interrupt its work or
    # invoke a tool, and use the ordinary account/credential validation path.
    await service.smart_tools.connect(server_id, expected_configuration=configuration)
    async with service.lock:
        status = admit(service, args)  # Reject navigation/configuration races.
        if status['status'] == 'review_required':
            if args.get('reviewedContract') != status['review']['fingerprint']:
                return status
        elif status['status'] not in {'ready', 'reconnect_required'}:
            raise AppError(status['message'], 409, code=status['status'])
        canvas, _ = saved(service, args['canvasId'])
        binding = canvas['mcp']
        server = service.smart_tools._server(server_id)
        current = contract(service, binding)
        update = {'catalogRevision': server['catalogRevision'], 'contractFingerprint': current['fingerprint'],
                  'accountIdentity': account(server)}
        # A second attached client must not later persist an older copy of this
        # shared binding. Context and local iframe input remain client-owned.
        for record in [service._state, *service.clients.records.values()]:
            other = record.get('canvas', {})
            if other.get('id') == canvas['id'] and other.get('sessionId') == canvas['sessionId']:
                other['mcp'].update(copy.deepcopy(update))
        binding.update(update)
        remember(service.state, service.db)
        service._publish()
        return inspect(service, args['canvasId'])
