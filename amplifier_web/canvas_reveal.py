"""Reveal an artifact on its named owning host, never on an inferred browser device."""
import asyncio
import os
from pathlib import Path
import platform
import shutil

from .host_identity import local_host_identity


def status(instance_id):
    system=platform.system()
    available=(system=='Darwin' and Path('/usr/bin/open').is_file()
               or system=='Windows' and bool(shutil.which('explorer.exe'))
               or system=='Linux' and bool(os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY')) and bool(shutil.which('gdbus')))
    return {'host':local_host_identity(),'instanceId':instance_id,'available':bool(available),
            'label':'Reveal in Finder' if system=='Darwin' else 'Reveal in Explorer' if system=='Windows' else 'Reveal in file manager',
            'detail':'Opens on the named app host, which may be a different computer from this browser. Copy its path or download the saved artifact to use it on this device.'}


def arguments(path):
    system=platform.system()
    if system=='Darwin':return ['/usr/bin/open','-R',str(path)]
    if system=='Windows':return [shutil.which('explorer.exe'),'/select,'+str(path)]
    if system=='Linux':return [shutil.which('gdbus'),'call','--session','--dest','org.freedesktop.FileManager1',
        '--object-path','/org/freedesktop/FileManager1','--method','org.freedesktop.FileManager1.ShowItems',
        "['"+path.as_uri()+"']",'']
    raise ValueError('This host has no supported file manager. Copy the path or download the artifact.')


async def reveal(path,instance_id,args):
    current=status(instance_id)
    if args['hostId']!=current['host']['id'] or args['instanceId']!=instance_id:
        raise ValueError('The owning app host changed. Refresh this artifact before revealing it.')
    if not path or not Path(path).is_absolute():raise ValueError('This artifact has no saved host file path.')
    target=Path(path)
    if not target.exists():raise ValueError('The original file no longer exists on '+current['host']['label']+'. You can still download a saved artifact or copy its path.')
    if not current['available']:raise ValueError('No supported desktop file manager is available on '+current['host']['label']+'. Copy the host path or download the saved artifact.')
    child=await asyncio.create_subprocess_exec(*arguments(target),stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL)
    try:await asyncio.wait_for(child.wait(),8)
    except TimeoutError:
        child.kill();await child.wait()
        raise ValueError('The host file manager did not acknowledge the request. Its outcome is unknown; check that host before trying again.') from None
    if child.returncode:raise ValueError('The host file manager declined the request. Check desktop access on '+current['host']['label']+'.')
    return {'status':'requested','host':current['host'],'message':'Reveal requested on '+current['host']['label']+'. This does not open a file manager on a different browser device.'}
