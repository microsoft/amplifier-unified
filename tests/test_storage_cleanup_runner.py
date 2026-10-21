from pathlib import Path
from types import SimpleNamespace
import subprocess

import pytest

from amplifier_web.storage_cleanup_runner import inspect
from amplifier_web import managed_runner_updates as runner, update_retention as retention


def git(path, *args):
    return subprocess.check_output(['git', *args], cwd=path, text=True).strip()


@pytest.fixture
def owned(tmp_path, monkeypatch):
    folder = tmp_path/'smart-tools/installs'/('a'*24)
    source = folder/'source'; source.mkdir(parents=True)
    git(source,'init','-q'); git(source,'config','user.name','Fixture'); git(source,'config','user.email','fixture@example.invalid')
    (source/'code.py').write_text('answer=42\n'); git(source,'add','.'); git(source,'commit','-qm','Installed source')
    item={'id':'a'*24,'commit':git(source,'rev-parse','HEAD')}
    app=SimpleNamespace(data_dir=tmp_path,update_manager=SimpleNamespace(),smart_tools=SimpleNamespace(state={'servers':[]}))
    monkeypatch.setattr(runner,'referenced',lambda *args:False)
    async def references(*args,**kwargs):return ''
    monkeypatch.setattr(retention,'inspect_references',references)
    original=retention._probe
    async def probe(operation, values, **kwargs):
        if operation=='runner':return None # Exact dependency runtime has separate native identity coverage.
        return await original(operation,values,**kwargs)
    monkeypatch.setattr(retention,'_probe',probe)
    return app,folder,source,item,[{'candidate':{'installationId':item['id']},'runtime':{'fixture':True}}]


async def test_clean_exact_git_copy_is_measured(owned):
    app,folder,source,item,records=owned
    result=await inspect(app,item,records)
    assert result['fileBytes']>0 and result['paths']==[str(folder)]


@pytest.mark.parametrize('change',['dirty','untracked','ignored','local-commit','redirect','outside-source','other-branch'])
async def test_user_source_changes_remain_protected(owned,tmp_path,change):
    app,folder,source,item,records=owned
    if change=='dirty':(source/'code.py').write_text('user work')
    elif change=='untracked':(source/'notes').write_text('user work')
    elif change=='ignored':
        (source/'.git/info/exclude').write_text('notes\n');(source/'notes').write_text('user work')
    elif change=='local-commit':
        (source/'code.py').write_text('user work');git(source,'commit','-qam','Unpublished local work')
    elif change=='other-branch':
        git(source,'checkout','-qb','private-work');(source/'code.py').write_text('user work');git(source,'commit','-qam','Unpublished branch');git(source,'checkout','-q',item['commit'])
    elif change=='outside-source':(folder/'notes').write_text('user work')
    else:git(source,'config','core.worktree',str(tmp_path))
    with pytest.raises(ValueError):await inspect(app,item,records)
    assert source.exists()


async def test_registered_connection_keeps_exact_copy(owned):
    app,folder,source,item,records=owned
    app.smart_tools.state['servers']=[{'args':[str(folder/'runtime/bin/python')]}]
    with pytest.raises(ValueError,match='connection'):await inspect(app,item,records)


@pytest.mark.parametrize('change',['policy','process'])
async def test_runner_waiting_for_install_lock_rechecks_removal_admission(owned,monkeypatch,change):
    import asyncio
    from amplifier_web.smart_tool_lifecycle import Lifecycle
    from amplifier_web import test_environment_setup as private, task_environments
    app,folder,source,item,records=owned
    item.update(runnerCandidate=True,repository=task_environments.REPOSITORY)
    app.smart_tools.state['installations']=[item]
    app.smart_tools.root=app.data_dir/'smart-tools';app.smart_tools.service=app
    app.smart_tools.install_lock=asyncio.Lock()
    async def mutate(function):function(app.smart_tools.state)
    app.smart_tools._change=mutate
    app.smart_tools.uninstall=Lifecycle.uninstall.__get__(app.smart_tools)
    private.write_json(runner.record_path(app,'c'*32),{**records[0],'state':'superseded'})
    ready=asyncio.Event();original=inspect
    async def inspected(*args):
        result=await original(*args);ready.set();return result
    from amplifier_web import storage_cleanup_runner
    monkeypatch.setattr(storage_cleanup_runner,'inspect',inspected)
    enabled=True;references='';effects=[]
    async def observe(*args,**kwargs):return references
    monkeypatch.setattr(retention,'inspect_references',observe)
    def evidence(status,row):
        if status=='attempting' and not enabled:raise ValueError('Policy disabled')
        effects.append(status)
    await app.smart_tools.install_lock.acquire()
    task=asyncio.create_task(runner.retire(app,selected={item['id']},evidence=evidence))
    await asyncio.wait_for(ready.wait(),2)
    if change=='policy':enabled=False
    else:references=str(folder)
    app.smart_tools.install_lock.release()
    await asyncio.wait_for(task,3)
    assert folder.exists() and app.smart_tools.state['installations']==[item] and not effects
