"""Private atomic starter state and exact placement identity checks."""
import json
import os
from pathlib import Path
import stat
import uuid

def atomic(path, value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    temporary=path.with_name('.'+path.name+'.'+uuid.uuid4().hex)
    fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    try:
        with os.fdopen(fd,'w') as stream:
            json.dump(value,stream,ensure_ascii=False);stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,path)
        directory=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(directory)
        finally:os.close(directory)
    finally:temporary.unlink(missing_ok=True)

def _validate_created_directory(receipt):
    """A placement receipt may need recovery before the app commits registration."""
    path = Path(receipt['path'])
    try:
        info = path.lstat()
        unchanged = (stat.S_ISDIR(info.st_mode)
                     and [info.st_dev, info.st_ino] == receipt.get('directoryIdentity')
                     and path.resolve(strict=True) == path)
    except (OSError, RuntimeError):
        unchanged = False
    if not unchanged:
        raise ValueError('The created folder changed. Inspect it before attaching it again.')

