"""Opt-in model visual inspection of an unknown synthetic local PNG.

Uses new isolated history and the selected configured provider. Raw credentials
and provider history are private; only bounded acceptance booleans are printed.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys

from acceptance import Observation, installed_revisions, private_json, setup
from parity_acceptance import profile


async def run(args):
    from aiohttp import web
    from PIL import Image, ImageDraw, ImageFont
    import amplifier_web.runtime_worker as worker
    from amplifier_web.server import create_app
    from amplifier_web.session_client import SessionClient

    folder, provider = setup(args)
    bundle = await profile(folder, args)
    page = Image.new('RGB', (600, 300), 'white')
    drawing = ImageDraw.Draw(page)
    drawing.ellipse((40, 55, 230, 245), fill=(130, 30, 185))
    drawing.text((350, 95), '67', fill='black', font=ImageFont.load_default(size=80))
    page.save(folder/'workspace/sample.png')
    report = {'provider': args.provider, 'model': provider['config'].get('default_model'),
              'evidence': 'real configured provider, isolated Unified worker, exact saved image typed delivery',
              'installed': installed_revisions(), 'checks': {}, 'passed': False}
    app = await create_app(folder/'app',workspace=folder/'workspace',voice=False,background_updates=False,preload_providers=False)
    service=app['service'];service.runtime.command=[sys.executable,str(Path(worker.__file__).resolve())]
    observed=Observation();original=service.on_runtime_event
    async def event(kind,data):
        observed.add(kind,data);await original(kind,data)
    service.on_runtime_event=event
    runner=web.AppRunner(app);await runner.setup();site=web.TCPSite(runner,'127.0.0.1',0);await site.start()
    url=f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}'
    try:
        async with SessionClient(url,app['control_token'],'image-acceptance') as client:
            created=await client.create_session({'title':'Synthetic visual QA','bundle':str(bundle),'workspace':str(folder/'workspace')},command_id='create')
            sid=created['state']['selectedSessionId'];report['sessionId']=sid
            await service.runtime.start(service._session(sid),service.on_runtime_event)
            print(json.dumps({'phase':'image-runtime-ready'}),flush=True)
            await client.command(sid,'conversation.send',{'text':
                'Perform one bounded visual acceptance check. Use app_control list_actions to discover outputs.attach and outputs.image. '
                'Attach sample.png from this workspace as a file output and request its actual pixels using outputs.image with the saved id and sha256. '
                'Do not use shell or file-content tools to infer the image. After you receive typed pixels, report the two-digit number on the right '
                'and the shape/color on the left. If no pixels arrived, say that explicitly. Do not delegate, schedule, or create goals. End with IMAGE_CHECK_DONE.'},command_id='inspect')
            await observed.wait(lambda:any(event.get('kind')=='assistant.message' and event.get('sessionId')==sid and 'IMAGE_CHECK_DONE' in event.get('text','') for event in observed.events) and service._session(sid)['status']=='idle',240)
            history=await service.runtime.control(sid,'history.snapshot');private_json(folder/'history.json',history)
            calls=[]
            for message in history['messages']:
                for call in message.get('tool_calls') or []:
                    name=call.get('tool') or call.get('name') or call.get('function',{}).get('name')
                    arguments=call.get('arguments') or call.get('function',{}).get('arguments',{})
                    if isinstance(arguments,str):
                        try:arguments=json.loads(arguments)
                        except ValueError:arguments={}
                    calls.append({'name':name,'arguments':arguments})
            text='\n'.join(event.get('text','') for event in observed.events if event.get('kind')=='assistant.message' and event.get('sessionId')==sid).lower()
            report['checks']['explicit_image_action']=any(row['name']=='app_control' and row['arguments'].get('args',{}).get('action')=='outputs.image' for row in calls)
            report['checks']['correct_unprompted_number']='67' in text
            report['checks']['correct_unprompted_shape']='circle' in text
            report['checks']['correct_unprompted_color']=any(color in text for color in ['purple','violet'])
            report['checks']['no_alternate_file_reads']=all(row['name']=='app_control' for row in calls)
            records=service.outputs.store.list(sid)['items']
            report['checks']['saved_exact_image']=any(row['filename']=='sample.png' and row['versionEvidence']=='snapshot' for row in records)
            report['usage']=await service.runtime.control(sid,'usage.inspect')
            report['passed']=all(report['checks'].values())
    except Exception as exc:
        report['error_type']=type(exc).__name__; (folder/'error.txt').write_text(str(exc))
        for sid,row in service.runtime.workers.items():private_json(folder/(sid+'-diagnostics.json'),row.get('stderr',[]))
    finally:
        await runner.cleanup();private_json(folder/'report.json',report);private_json(folder/'events.json',observed.events)
    print(json.dumps({'passed':report['passed'],'checks':report['checks'],'error_type':report.get('error_type'),'report':str(folder/'report.json')}),flush=True)
    return report['passed']


def main():
    os.umask(0o077)
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--allow-live',action='store_true');parser.add_argument('--provider',required=True)
    parser.add_argument('--settings',type=Path,default=Path.home()/'.amplifier/settings.yaml')
    parser.add_argument('--bundle',type=Path,required=True);parser.add_argument('--module-root',type=Path);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--module-source',action='append',default=[])
    args=parser.parse_args()
    if not args.allow_live:parser.error('--allow-live is required for paid provider calls')
    raise SystemExit(0 if asyncio.run(run(args)) else 1)


if __name__=='__main__':main()
