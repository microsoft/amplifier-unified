import asyncio
import threading
from types import SimpleNamespace

import pytest

from amplifier_unified_workspaces.storage_scans import StorageScans
from amplifier_unified_workspaces import storage_usage


async def test_progress_is_client_scoped_cancelled_and_counted_until_walker_finishes(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    def inspect(roots, args, **options):
        options['progress']({'version':1,'rows':[{'label':'Measured first','bytes':3}],'bytes':3,'incomplete':True})
        entered.set()
        assert release.wait(2)
        assert options['stopped'].is_set()
        return {'version':1,'rows':[],'bytes':3,'incomplete':True}
    monkeypatch.setattr(storage_usage,'inspect',inspect)
    owner=SimpleNamespace(intake=SimpleNamespace(background=0),on_idle=None,closed=False)
    scans=StorageScans(owner)
    try:
        first=await scans.read({'progressive':True},'client-a',[])
        assert first['scanning'] and first['bytes'] is None and owner.intake.background==1
        assert await asyncio.to_thread(entered.wait,1)
        result=await scans.read({'progressive':True,'scanId':first['scanId']},'client-a')
        assert result['bytes']==3 and result['scanning']
        for args,client in [({'scanId':first['scanId']},'client-b'),({'scanId':first['scanId'],'scope':'history'},'client-a')]:
            with pytest.raises(ValueError,match='expired'):await scans.read(args,client)
        result=await scans.read({'scanId':first['scanId'],'cancel':True},'client-a')
        assert result['cancelled'] and not result['scanning']
        assert owner.intake.background==1, 'cancellation cannot hide a running filesystem walker'
    finally:
        release.set()
        await scans.close()
    assert owner.intake.background==0


def test_time_budget_and_stop_are_honest_lower_bounds(tmp_path):
    (tmp_path/'value').write_bytes(b'x'*20)
    roots=[{'id':'app','scope':'app','label':'App','path':str(tmp_path),'category':'other'}]
    result=storage_usage.inspect(roots,{},seconds=0)
    assert result['bytes']==0 and result['incomplete']
    stop=threading.Event();stop.set()
    assert storage_usage.inspect(roots,{},stopped=stop)['incomplete']
    result=storage_usage.inspect(roots,{})
    assert result['bytes']==20 and not result['incomplete']


async def test_close_drains_evicted_walkers(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    def inspect(*args, **options):
        entered.set()
        assert release.wait(2)
        return {'version':1,'rows':[],'bytes':0,'incomplete':False}
    monkeypatch.setattr(storage_usage,'inspect',inspect)
    owner=SimpleNamespace(intake=SimpleNamespace(background=0),on_idle=None,closed=False)
    scans=StorageScans(owner)
    try:
        await scans.read({'progressive':True},'same',[])
        assert await asyncio.to_thread(entered.wait,1)
        await scans.read({'progressive':True},'same',[])
        assert len(scans.entries)==1 and len(scans.tasks)==2
    finally:
        release.set()
        await scans.close()
    assert owner.intake.background==0 and not scans.tasks


def test_drill_reuses_complete_observations_preserving_hardlink_accounting(tmp_path, monkeypatch):
    folder=tmp_path/'saved';folder.mkdir();(folder/'one').write_bytes(b'x'*13)
    storage_usage.os.link(folder/'one',folder/'same')
    roots=[{'id':'app','scope':'app','label':'App','path':str(tmp_path),'category':'other'}]
    cache, seen = {}, {}
    assert storage_usage.inspect(roots,{},cache=cache,seen=seen)['bytes']==13
    walked=[];original=storage_usage.os.scandir
    def scandir(fd):
        walked.append(fd);return original(fd)
    monkeypatch.setattr(storage_usage.os,'scandir',scandir)
    result=storage_usage.inspect(roots,{'scope':'app','rootId':'app'},cache=cache,seen=seen)
    assert result['bytes']==13 and not result['incomplete']
    assert len(walked)==1, 'opening the category must not walk its measured subtrees again'
    (folder/'new').write_bytes(b'y'*7)
    result=storage_usage.inspect(roots,{'scope':'app','rootId':'app'},cache=cache,seen=seen)
    assert result['bytes']==20, 'a changed directory fingerprint invalidates its cached observation'


async def test_scan_reuse_is_bounded_by_client_root_set_and_age(tmp_path):
    roots=[{'id':'app','scope':'app','label':'App','path':str(tmp_path),'category':'other'}]
    (tmp_path/'one').write_bytes(b'x')
    owner=SimpleNamespace(intake=SimpleNamespace(background=0),on_idle=None,closed=False)
    scans=StorageScans(owner)
    try:
        first=await scans.read({'progressive':True},'a',roots)
        old=scans.entries[first['scanId']];await old.task
        args={'progressive':True,'fromScanId':first['scanId'],'scope':'category','categoryId':'other'}
        second=await scans.read(args,'a',roots);new=scans.entries[second['scanId']];await new.task
        assert old.cache is new.cache and old.measured_at==new.measured_at
        for client, changed, stale in [('b',roots,False),('a',[],False),('a',roots,True)]:
            if stale:old.touched-=301
            item=await scans.read(args,client,changed);scan=scans.entries[item['scanId']];await scan.task
            assert scan.cache is not old.cache
    finally:await scans.close()


async def test_owner_advertises_progress_and_holds_shutdown_until_scan_is_drained(tmp_path, monkeypatch):
    from amplifier_unified_workspaces.owner import Owner
    from test_owner import CatalogFixture, config, FENCE
    entered,release=threading.Event(),threading.Event()
    def inspect(*args, **kwargs):
        entered.set();assert release.wait(2)
        return {'version':1,'rows':[],'bytes':0,'incomplete':False}
    monkeypatch.setattr(storage_usage,'inspect',inspect)
    owner=Owner(config(tmp_path),CatalogFixture())
    try:
        snapshot=await owner.request('snapshot',{'clientId':'a'})
        assert snapshot['storageMeasurements']=={'version':1,'progressive':True}
        result=await owner.request('action',{'operation':'storage.inspect','args':{'progressive':True},'clientId':'a'})
        assert await asyncio.to_thread(entered.wait,1)
        observation=await owner.request('quiescence/inspect',{})
        assert observation['background']==1
        assert not (await owner.request('quiescence/acquire',FENCE))['acquired']
        await owner.request('action',{'operation':'storage.inspect','args':{'scanId':result['scanId'],'cancel':True},'clientId':'a'})
        assert (await owner.request('quiescence/inspect',{}))['background']==1
        release.set();await owner.storage_scans.close()
        assert (await owner.request('quiescence/acquire',{**FENCE,'fenceId':'storage-drained','commandId':'storage-drained'}))['acquired']
        with pytest.raises(ValueError,match='paused'):
            await owner.request('action',{'operation':'storage.inspect','args':{'progressive':True},'clientId':'a'})
    finally:release.set();await owner.close()


def test_large_observation_resumes_entry_budget_without_revisiting_and_keeps_totals(tmp_path, monkeypatch):
    import os
    root = tmp_path/'root'; root.mkdir()
    nested = root/'nested'; nested.mkdir()
    for n in range(37): (nested/str(n)).write_bytes(b'x'*11)
    os.link(nested/'0', nested/'same')
    (root/'excluded').symlink_to(tmp_path/'outside')
    roots=[{'id':'app','scope':'app','label':'App','path':str(root),'category':'other'}]
    monkeypatch.setattr(storage_usage,'LIMIT',3)
    cache, seen, walks, listings, measurements = {}, {}, {}, {}, {}
    observed=[]; stat=storage_usage.os.stat
    def count(name,*args,**kwargs):
        if kwargs.get('dir_fd') is not None and str(name).isdigit(): observed.append(name)
        return stat(name,*args,**kwargs)
    monkeypatch.setattr(storage_usage.os,'stat',count)
    totals=[]
    try:
        for _ in range(80):
            snapshots=[]
            value=storage_usage.inspect(roots,{},cache=cache,seen=seen,walks=walks,listings=listings,measurements=measurements,progress=snapshots.append)
            totals.extend(row['bytes'] for row in snapshots)
            if not value['measuring']:break
        assert not value['measuring'] and value['bytes']==37*11
        assert value['incomplete'], 'excluded link stays a lower bound, never a permanently pending walk'
        assert totals==sorted(totals)
        assert len(observed)==37 and len(seen)==1
        assert all(w.done and not w.stack and w.parent is None for w in walks.values())
        category=storage_usage.inspect(roots,{'scope':'category','categoryId':'other'},cache=cache,seen=seen,walks=walks,listings=listings,measurements=measurements)
        assert category['bytes']==37*11 and not category['measuring']
    finally:
        for walk in walks.values():walk.close()


def test_folder_cursor_resumes_a_time_slice_and_finishes_when_cache_is_full(tmp_path, monkeypatch):
    for n in range(30):(tmp_path/str(n)).write_bytes(b'x'*7)
    roots=[{'id':'app','scope':'app','label':'App','path':str(tmp_path),'category':'other'}]
    cache={str(n): (None,{}) for n in range(20_000)}
    walks,listings,measurements,seen={},{},{},{}
    ticks=0
    def expired():
        nonlocal ticks
        ticks+=1
        return ticks>13
    monkeypatch.setattr(storage_usage,'_expired',expired)
    counts=[]
    try:
        for _ in range(100):
            ticks=0
            value=storage_usage.inspect(roots,{'scope':'app','rootId':'app'},cache=cache,seen=seen,walks=walks,listings=listings,measurements=measurements)
            counts.append(value['bytes'])
            if not value['measuring']:break
        assert not value['measuring'] and not value['incomplete']
        assert value['bytes']==210 and len(value['rows'])==30
        assert counts==sorted(counts) and len(cache)<=20_000
    finally:
        for walk in walks.values():walk.close()
        for listing in listings.values():
            if listing.get('entries') is not None:listing['entries'].close()


async def test_idle_measurement_resumes_original_roots_and_does_not_hold_maintenance(monkeypatch):
    from amplifier_unified_workspaces import storage_scans
    calls=[]
    owner=SimpleNamespace(intake=SimpleNamespace(background=0,fence=None),on_idle=None,closed=False)
    scans=StorageScans(owner)
    def inspect(roots,args,**kwargs):
        calls.append(roots)
        if len(calls)==1:
            next(iter(scans.entries.values())).touched-=31
        return {'version':1,'rows':[], 'bytes':len(calls), 'measuring':len(calls)<2, 'incomplete':len(calls)<2}
    monkeypatch.setattr(storage_usage,'inspect',inspect)
    try:
        first=await scans.read({'progressive':True},'a',[{'id':'original'}])
        scan=scans.entries[first['scanId']];await scan.task
        assert scan.paused and owner.intake.background==0
        owner.intake.fence='maintenance'
        with pytest.raises(ValueError,match='paused'):await scans.read({'scanId':first['scanId']},'a')
        owner.intake.fence=None
        second=await scans.read({'scanId':first['scanId']},'a')
        assert second['scanning'] and owner.intake.background==1
        await scan.task
        assert calls==[[{'id':'original'}],[{'id':'original'}]]
        assert not scan.paused and not scan.result['measuring'] and owner.intake.background==0
    finally:await scans.close()


def test_navigation_out_of_a_full_frontier_set_makes_progress(tmp_path, monkeypatch):
    roots=[]
    for n in range(10):
        path=tmp_path/str(n);path.mkdir();(path/'one').write_bytes(b'abc')
        roots.append({'id':str(n),'scope':'app','label':str(n),'path':str(path),'category':'other'})
    walks, cache, seen, listings, measurements = {}, {}, {}, {}, {}
    monkeypatch.setattr(storage_usage,'LIMIT',1)
    try:
        first=storage_usage.inspect(roots,{},walks=walks,cache=cache,seen=seen,listings=listings,measurements=measurements)
        assert first['measuring'] and sum(not w.done for w in walks.values())==8
        for _ in range(50):
            value=storage_usage.inspect(roots,{'scope':'app','rootId':'9'},walks=walks,cache=cache,seen=seen,listings=listings,measurements=measurements)
            if not value['measuring']:break
        assert not value['measuring'] and value['bytes']==3
    finally:
        for walk in walks.values():walk.close()
        for listing in listings.values():
            if listing.get('entries') is not None:listing['entries'].close()
