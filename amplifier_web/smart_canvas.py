"""MCP Apps canvas bindings. Tool documents never receive the host's authority."""
from __future__ import annotations

import copy
import hashlib
import json
import time
import uuid

from .canvas_library import remember
from . import service as api


def configuration_key(server):
    config = {k: server.get(k) for k in ('id', 'command', 'args', 'env', 'cwd')}
    return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()


def definitions(schema, string):
    identity = {'id': string(100)}
    source = {'repository': string(2000), 'ref': string(500), 'path': string(1000)}
    return {
        'smartTools.catalog': ('Refresh the public Smart Tools catalog; listing does not install or run code.', schema()),
        'smartTools.inspect': ('Read a Git Smart Tool descriptor and manifest without running its code.', schema(source, ['repository'])),
        'smartTools.install': ('Install a Python Git Smart Tool in an isolated environment. Does not start it or configure models.', schema({**source, 'extras': {'type':'array','maxItems':20,'items':string(100)}}, ['repository'])),
        'smartTools.configure': ('Register or edit an MCP stdio server. env maps child variable names to host environment variable names, never secret values.', schema({**identity, 'name':string(200), 'command':string(4000), 'args':{'type':'array','maxItems':100,'items':string(4000)}, 'env':{'type':'object','maxProperties':50,'additionalProperties':string(200)}, 'cwd':string(4000)}, ['name','command'])),
        'smartTools.connect': ('Start this configured MCP server and discover its tools.', schema(identity)),
        'smartTools.disconnect': ('Disconnect this MCP server. Tool-owned durable work may continue independently.', schema(identity)),
        'smartTools.remove': ('Remove this server registration, keeping installed files and tool-owned work.', schema(identity)),
        'smartTools.call': ('Call a discovered model-visible tool. Receipt operationId identifies the durable result under smartTools.operations. A timeout is not proof that tool-owned work stopped; inspect its status before retrying.', schema({**identity,'name':string(200),'arguments':{'type':'object'},'sessionId':string(200),'timeoutSeconds':{'type':'number','minimum':1,'maximum':300}}, ['id','name'])),
        'smartTools.result': ('Inspect an older or large operation result in /smartTools/inspectedOperation. Follow $resource state paths for paged content.', schema({'operationId':string(100)})),
        'smartTools.open': ('Open a discovered MCP App in a durable canvas tab. Supply operationId to send that call’s arguments and result to the view. Closing a tab does not cancel tool work.', schema({**identity,'tool':string(200),'operationId':string(100),'sessionId':string(200)}, ['id','tool'])),
        'smartTools.appCall': ('Call an app-visible tool through the current canvas binding. Cannot select a different server. Results also appear in shared smartTools.operations.', schema({'canvasId':string(100),'name':string(200),'arguments':{'type':'object'}}, ['canvasId','name'])),
        'smartTools.context': ('Record the current MCP App view context as untrusted display data, visible to the agent on its next state read. Does not start a model turn.', schema({'canvasId':string(100),'context':{'type':'object'}}, ['canvasId','context'])),
    }


class SmartCanvas:
    def __init__(self, service):
        self.service = service

    def binding(self, identity):
        canvas = self.service.state.get('canvas', {})
        if canvas.get('id') != identity or canvas.get('kind') != 'mcp-app' or not canvas.get('open'):
            raise api.AppError('This tool view is no longer active. Reopen its canvas tab.', 409)
        binding = canvas['mcp']
        server = next((s for s in self.service.state['smartTools']['servers'] if s['id'] == binding['serverId']), None)
        if not server or configuration_key(server) != binding['configuration']:
            raise api.AppError('This server configuration changed. Open a fresh tool view.', 409)
        return canvas, binding

    async def command(self, action, args, operation_id, origin):
        manager = self.service.smart_tools
        if action == 'smartTools.open':
            record = {'id':operation_id,'action':action,'origin':origin,'target':copy.deepcopy(args),
                      'status':'running','createdAt':time.time(),'updatedAt':time.time()}
            async with self.service.lock:
                rows = self.service.state['smartTools']['operations']
                rows[:] = [r for r in rows if r.get('status') == 'running'] + [r for r in rows if r.get('status') != 'running'][-49:]
                rows.append(record)
                manager.persist_operation(record)
                self.service._publish()
            try:
                result = await self.open(args)
                record.update(status='completed',result=result)
            except Exception as exc:
                record.update(status='failed',error=str(exc)[:2000])
            async with self.service.lock:
                record['updatedAt'] = time.time()
                manager.persist_operation(record)
                self.service._publish()
            return
        if action == 'smartTools.appCall':
            try:
                canvas, binding = self.binding(args['canvasId'])
                if args['name'] not in binding['allowedTools']:
                    raise api.AppError('This tool was not granted to this canvas view.', 403)
            except Exception as exc:
                async with self.service.lock:
                    record = {'id':operation_id,'action':action,'origin':'app','target':{'canvasId':args['canvasId'],'name':args['name']},
                              'status':'failed','error':str(exc)[:2000],'createdAt':time.time(),'updatedAt':time.time()}
                    rows = self.service.state['smartTools']['operations']
                    rows[:] = [r for r in rows if r.get('status') == 'running'] + [r for r in rows if r.get('status') != 'running'][-49:]
                    rows.append(record)
                    manager.persist_operation(record)
                    self.service._publish()
                return
            mapped = {'id':binding['serverId'],'name':args['name'],'arguments':args.get('arguments',{}),
                      'sessionId':canvas.get('sessionId'), '_configuration':binding['configuration'],
                      '_allowedTools':binding['allowedTools']}
            result = await manager.command('smartTools.call', mapped, operation_id, 'agent' if origin == 'agent' else 'app')
            # Context is descriptive, not authority. Retain the latest result only for
            # this view; never replace its launch arguments with another tool's args.
            if result is not None:
                async with self.service.lock:
                    active = self.service.state.get('canvas', {})
                    if active.get('id') == args['canvasId']:
                        active['mcp']['lastOperationId'] = operation_id
                        self.service._publish()
            return
        return await manager.command(action, args, operation_id, origin)

    async def open(self, args):
        state = self.service.state
        server = next((s for s in state['smartTools']['servers'] if s['id'] == args['id']), None)
        if not server:
            raise api.AppError('Configure and connect the tool server first.')
        tool = next((t for t in server.get('tools',[]) if t['name'] == args['tool']), None)
        ui = (tool or {}).get('_meta',{}).get('ui',{})
        uri = ui.get('resourceUri')
        if not uri:
            raise api.AppError('This tool does not advertise an MCP App.')
        key = configuration_key(server)
        session = self.service._session(args.get('sessionId'))
        sid = session['id']
        workspace = next(w for w in state['workspaces'] if w['path'] == session['workspace'])
        operation = None
        if args.get('operationId'):
            operation = self.service.smart_tools.operation(args['operationId'])
            if not operation or operation.get('status') != 'completed' or operation.get('target',{}).get('id') != args['id'] or operation.get('target',{}).get('name') != args['tool'] or operation.get('target',{}).get('sessionId') not in {None,sid} or operation.get('configuration') != key:
                raise api.AppError('Choose a completed call to this tool from this conversation.')
        app = await self.service.smart_tools.read_app(args['id'], uri)
        async with self.service.lock:
            latest = next((s for s in state['smartTools']['servers'] if s['id'] == args['id']), None)
            if not latest or configuration_key(latest) != key:
                raise api.AppError('The server changed while loading its view. Try again.')
            self.service._session(sid)
            canvas = {'id':uuid.uuid4().hex,'kind':'mcp-app','open':True,'title':tool.get('title') or server['name'],
                      'content':app['html'],'sessionId':sid,'workspaceId':workspace['id'],
                      'view':{},'events':[],'renderReports':{},'createdAt':time.time(),
                      'mcp':{'serverId':args['id'],'configuration':key,'resourceUri':uri,'tool':args['tool'],
                             'allowedTools':app['tools'], 'operationId':args.get('operationId'),
                             'toolArguments':copy.deepcopy((operation or {}).get('arguments',{})),
                             'requestedCsp':app.get('csp',{}),'requestedPermissions':app.get('permissions',{}),
                             'context':{}}}
            scoped = {**state,'canvas':canvas,'selectedSessionId':sid,'selectedWorkspaceId':workspace['id']}
            remember(scoped,self.service.db)
            if state.get('selectedSessionId') == sid and state.get('selectedWorkspaceId') == workspace['id']:
                state['canvas'] = canvas
                state['view'].setdefault('canvasDraft',{}).update(library=False,open=False,browser=False)
            self.service._publish()
            return {'canvasId':canvas['id'],'resourceUri':uri}

    def context(self, args):
        canvas, _ = self.binding(args['canvasId'])
        context = args['context']
        if len(json.dumps(context).encode()) > 16000:
            raise api.AppError('App view context must be 16 KB or smaller.')
        canvas['mcp']['context'] = copy.deepcopy(context)
        canvas['mcp']['contextUpdatedAt'] = time.time()


def document_response(canvas):
    from aiohttp import web
    # Self-contained resources only in this first profile. Advertise the exact
    # denied permissions to Apps, rather than silently granting network access.
    return web.Response(text=canvas['content'], content_type='text/html', headers={
        'Content-Security-Policy': "sandbox allow-scripts allow-downloads; default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; font-src data:; media-src data: blob:; connect-src 'none'; frame-src data: blob:; object-src 'none'; form-action 'none'; base-uri 'none'; frame-ancestors 'self'",
        'Permissions-Policy': 'camera=(), microphone=(), geolocation=(), clipboard-read=(), clipboard-write=()'})
