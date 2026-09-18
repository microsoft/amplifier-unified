"""One-shot, pipeline and direct-tool workflows over the same app actions."""
import asyncio
import json
from pathlib import Path
import ssl
import sys
import uuid
import aiohttp
from aiohttp import web


def _connection_context(data_dir: Path, config: dict) -> ssl.SSLContext | None:
    if config["tls"]["method"] == "none":
        return None
    ca = data_dir / "config" / "tls" / "ca.crt"
    if not ca.is_file():
        raise ValueError("Configured HTTPS requires the app-owned local CA.")
    return ssl.create_default_context(cafile=str(ca))


def _connection_origins(args, config: dict) -> list[str]:
    """Configured origins first; the loopback address remains the local fallback."""
    scheme = "https" if config["tls"]["method"] != "none" else "http"
    local = f"{scheme}://127.0.0.1:{args.port}"
    return list(dict.fromkeys([*config["public_origins"], local]))


async def _existing_host(client: aiohttp.ClientSession, origins: list[str], data_identity: str) -> str | None:
    """Find a configured host with the app's identity, never trusting port ownership."""
    invalid_host = False
    for base in origins:
        try:
            async with client.get(base + "/api/health", timeout=aiohttp.ClientTimeout(total=2)) as response:
                if response.status != 200:
                    continue
                connected = await response.json()
        except (aiohttp.ClientError, TimeoutError, ValueError):
            continue
        if connected.get("app") == "amplifier-unified" and connected.get("dataIdentity") == data_identity:
            return base
        invalid_host = True
    if invalid_host:
        raise ValueError("A configured origin belongs to a different or older host. Choose its matching data directory, update it, or select another port.")
    return None


async def run(args, *, config=None):
    runner = None
    from .auth import control_token
    from .deployment import load_server_config
    data_dir = Path(args.data_dir)
    config = config if config is not None else load_server_config(data_dir, overrides={"port": args.port})
    context = _connection_context(data_dir, config)
    headers = {"Authorization": "Bearer " + control_token(data_dir)}
    import hashlib
    expected = hashlib.sha256(str(data_dir.expanduser().resolve()).encode()).hexdigest()
    async with aiohttp.ClientSession(headers=headers, connector=aiohttp.TCPConnector(ssl=context)) as client:
        base = await _existing_host(client, _connection_origins(args, config), expected)
        if base is None:
            from .server import create_app
            runner = web.AppRunner(await create_app(data_dir, workspace=args.workspace, background_updates=False,
                                                    server_config=config))
            await runner.setup(); site = web.TCPSite(runner, "127.0.0.1", 0); await site.start()
            port = site._server.sockets[0].getsockname()[1]; base = f"http://127.0.0.1:{port}"
        async def state():
            async with client.get(base+'/api/state') as response:return await response.json()
        async def dispatch(action,values):
            identity=str(uuid.uuid4())
            async with client.post(base+'/api/actions',json={'action':action,'args':values,'id':identity}) as response:
                payload=await response.json()
                if response.status>=400:raise RuntimeError(payload.get('error','Action failed'))
                return {**payload,'commandId':identity}
        async def answer_approvals(snapshot,identity):
            session=next((s for s in snapshot['sessions'] if s['id']==identity),{})
            for approval in session.get('approvals',[]):
                if approval.get('status')!='pending':continue
                decision='deny'
                if sys.stdin.isatty():
                    print(approval.get('prompt','Approval required'),file=sys.stderr)
                    print('Allow this operation? [y/N] ',end='',file=sys.stderr,flush=True)
                    answer=await asyncio.to_thread(sys.stdin.readline)
                    decision='allow' if answer.strip().lower() in {'y','yes'} else 'deny'
                await dispatch('approval.respond',{'id':approval['id'],'decision':decision})

        async def control(identity,operation,values):
            receipt=await dispatch('runtime.control',{'sessionId':identity,'operation':operation,'args':values})
            while True:
                snapshot=await state();management=snapshot.get('managementResults',{}).get(receipt['commandId'],{})
                await answer_approvals(snapshot,identity)
                if management.get('phase')=='error':raise RuntimeError(management.get('error'))
                value=snapshot.get('runtimeControl',{}).get(identity,{}).get(operation)
                if management.get('phase')=='ready' and value is not None:return value
                await asyncio.sleep(.2)
        try:
            snapshot=await state()
            identity=getattr(args,'resume',None)
            if args.command=='continue' and not identity:identity=snapshot.get('selectedSessionId') or next((s['id'] for s in snapshot['sessions']),None)
            if identity:await dispatch('session.select',{'id':identity})
            else:
                result=await dispatch('session.create',{'title':getattr(args,'prompt','')[:70] or 'Terminal task','workspace':args.workspace,'bundle':getattr(args,'bundle',None) or snapshot['settings']['bundle']})
                identity=result['state']['selectedSessionId']
            if getattr(args,'provider',None) or getattr(args,'model',None):
                await control(identity,'provider.select',{'provider':args.provider,'model':args.model})
            if getattr(args,'max_tokens',None):await control(identity,'budget.set',{'maxOutputTokens':args.max_tokens})
            if args.command=='tool':
                result=await control(identity,'tool.invoke',{'name':args.name,'arguments':json.loads(args.args)})
                print(json.dumps(result,ensure_ascii=False,indent=2));return 0
            prompt=args.prompt or ''
            if not prompt and not sys.stdin.isatty():prompt=sys.stdin.read()
            if not prompt.strip():raise ValueError('Provide a prompt or pipe text into this command')
            receipt=await dispatch('conversation.send',{'sessionId':identity,'text':prompt,'via':'chat'})
            submitted=receipt['state']['sessions'];session=next(s for s in submitted if s['id']==identity)
            baseline=next(i+1 for i,m in enumerate(session['messages']) if m.get('inputId')==receipt['commandId'])
            async with asyncio.timeout(args.timeout):
                while True:
                    snapshot=await state();session=next(s for s in snapshot['sessions'] if s['id']==identity)
                    await answer_approvals(snapshot,identity)
                    if session['status']=='error':raise RuntimeError(session.get('error','Execution failed'))
                    if session['status'] in {'stopped','interrupted'}:raise RuntimeError('Execution stopped before completion')
                    if session['status']=='idle' and len(session['messages'])>baseline:
                        response='\n\n'.join(m['text'] for m in session['messages'][baseline:] if m['role']=='assistant')
                        if args.output_format=='text':print(response)
                        else:
                            turn=next((t for t in session.get('execution',{}).get('turns',[]) if t['id']==receipt['commandId']),{})
                            result={'sessionId':identity,'response':response,'usage':turn.get('aggregateUsage')}
                            if args.output_format=='json-trace':result['execution']=session.get('execution',{});result['events']=session.get('runtimeEvents',[])
                            print(json.dumps(result,ensure_ascii=False))
                        return 0
                    await asyncio.sleep(.3)
        finally:
            if runner:await runner.cleanup()
