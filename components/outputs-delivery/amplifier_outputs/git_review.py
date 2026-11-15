"""Bounded read-only Git snapshots. Never invokes hooks, filters or external diff."""
import asyncio
import hashlib
import os
import re


async def _command(cwd, *args, limit=2_000_000, overrides=(), allow_missing=False):
    env = {**os.environ,'GIT_OPTIONAL_LOCKS':'0','GIT_TERMINAL_PROMPT':'0','GIT_CONFIG_NOSYSTEM':'1'}
    process = await asyncio.create_subprocess_exec('git','--literal-pathspecs','-c','core.fsmonitor=false','-c','core.pager=cat','-c','core.hooksPath=/dev/null',*overrides,'-C',str(cwd),*args,
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
        if process.returncode and not (allow_missing and process.returncode==1):
            raise ValueError('Git review could not resolve this repository or revision: '+stderr.decode(errors='replace')[:300])
        return stdout
    finally:
        if process.returncode is None:
            process.kill()
        await process.wait()


async def command(cwd, *args, limit=2_000_000):
    # --no-textconv does not stop Git's clean/smudge/process conversion drivers.
    # Enumerating configuration is inert; command-line overrides dominate every
    # configured driver without rewriting repository or global configuration.
    keys=await _command(cwd,'config','--null','--name-only','--get-regexp',
        r'^filter\..*\.(clean|smudge|process|required)$',limit=65536,allow_missing=True)
    drivers={key.rsplit('.',1)[0] for key in keys.decode().split('\0') if key}
    overrides=[]
    for driver in sorted(drivers):
        for suffix,value in (('clean',''),('smudge',''),('process',''),('required','false')):
            overrides.extend(('-c',driver+'.'+suffix+'='+value))
    return await _command(cwd,*args,limit=limit,overrides=overrides)


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
        'notice':'Immutable diff evidence with clean/smudge/process filters disabled: raw Git and worktree bytes, including LFS pointers or locally materialized contents. Untracked files are excluded; no changes are applied. Binary contents are not in the text diff.'}


def _path(header, prefix):
    # Git terminates unquoted space-containing paths with a tab. Real filename
    # tabs are C-escaped inside a quoted path instead.
    header=header.split('\t',1)[0]
    if header.startswith('"'):
        # Git uses C quoting (including octal UTF-8 bytes), not JSON escaping.
        if not header.endswith('"'):return None
        raw=bytearray();index=1
        escapes={'a':7,'b':8,'t':9,'n':10,'v':11,'f':12,'r':13,'"':34,'\\':92}
        while index<len(header)-1:
            char=header[index];index+=1
            if char!='\\':raw.extend(char.encode());continue
            if index>=len(header)-1:return None
            match=re.match(r'[0-7]{1,3}',header[index:-1])
            if match:
                value=int(match[0],8)
                if value>255:return None
                raw.append(value);index+=len(match[0])
            else:
                char=header[index];index+=1
                if char not in escapes:return None
                raw.append(escapes[char])
        try:header=raw.decode('utf-8')
        except UnicodeDecodeError:return None
    return header[len(prefix):] if header.startswith(prefix) else None


def anchors(text):
    """Only exact displayed hunk lines can anchor a local review comment."""
    result=set();old_path=new_path=None;old=new=None
    for line in text.splitlines():
        if line.startswith('diff --git '):
            old_path=new_path=None;old=new=None
        elif old is None and new is None and line.startswith('--- '):
            old_path=_path(line[4:],'a/')
        elif old is None and new is None and line.startswith('+++ '):
            new_path=_path(line[4:],'b/')
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
