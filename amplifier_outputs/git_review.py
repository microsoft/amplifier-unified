"""Bounded read-only Git snapshots. Never invokes hooks, filters or external diff."""
import asyncio
import hashlib
import os
import re


async def command(cwd, *args, limit=2_000_000):
    env = {**os.environ,'GIT_OPTIONAL_LOCKS':'0','GIT_TERMINAL_PROMPT':'0','GIT_CONFIG_NOSYSTEM':'1'}
    process = await asyncio.create_subprocess_exec('git','--literal-pathspecs','-c','core.fsmonitor=false','-c','core.pager=cat','-C',str(cwd),*args,
        stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,env=env)
    async def read(stream):
        data = bytearray()
        while chunk:=await stream.read(65536):
            data.extend(chunk)
            if len(data)>limit:
                raise ValueError('Git review exceeded its output limit; choose a narrower file path.')
        return bytes(data)
    try:
        stdout,stderr,_ = await asyncio.wait_for(asyncio.gather(read(process.stdout),read(process.stderr),process.wait()),10)
        if process.returncode:
            raise ValueError('Git review could not resolve this repository or revision: '+stderr.decode(errors='replace')[:300])
        return stdout
    finally:
        if process.returncode is None:
            process.kill()
        await process.wait()


async def snapshot(cwd, mode, base=None, path=None):
    head = (await command(cwd,'rev-parse','--verify','HEAD^{commit}')).decode().strip()
    baseline = None
    if mode=='branch':
        if not base or base.startswith('-') or len(base)>200:
            raise ValueError('Choose an existing base revision.')
        baseline = (await command(cwd,'rev-parse','--verify','--end-of-options',base+'^{commit}')).decode().strip()
    args = ['diff','--no-ext-diff','--no-textconv','--no-color','--no-renames','--src-prefix=a/','--dst-prefix=b/','--unified=3']
    args += [baseline,head] if mode=='branch' else ['--cached',head] if mode=='staged' else []
    args += ['--']+([path] if path else [])
    data = await command(cwd,*args)
    if (await command(cwd,'rev-parse','--verify','HEAD^{commit}')).decode().strip()!=head:
        raise ValueError('The repository changed during review. Inspect it again.')
    # Repeat the same bounded read to reject an observed index/worktree race.
    if mode!='branch' and await command(cwd,*args)!=data:
        raise ValueError('The diff changed during review. Inspect it again.')
    text = data.decode('utf-8',errors='replace')
    return {'text':text,'sha256':hashlib.sha256(data).hexdigest(),'head':head,'base':baseline,'mode':mode,
        'notice':'Immutable diff evidence. Untracked files are excluded; no changes are applied. Binary contents are not in the text diff.'}


def anchors(text):
    """Only exact displayed hunk lines can anchor a local review comment."""
    result=set();old_path=new_path=None;old=new=None
    for line in text.splitlines():
        if line.startswith('diff --git '):
            old_path=new_path=None;old=new=None
        elif line.startswith('--- a/'):
            old_path=line[6:];old=None
        elif line.startswith('+++ b/'):
            new_path=line[6:];new=None
        elif match:=re.match(r'^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@',line):
            old,new=map(int,match.groups())
        elif old is not None and new is not None:
            if line.startswith('-'):
                if old_path:result.add((old_path,'left',old))
                old+=1
            elif line.startswith('+'):
                if new_path:result.add((new_path,'right',new))
                new+=1
            elif line.startswith(' '):
                if old_path:result.add((old_path,'left',old))
                if new_path:result.add((new_path,'right',new))
                old+=1;new+=1
    return result
