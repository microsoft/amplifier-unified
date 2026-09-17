"""Scoped application configuration; never writes another host's settings."""
from pathlib import Path
import copy
from filelock import FileLock
import yaml
from .host.config import read_yaml, write_private

class SettingsStore:
    def __init__(self,home):
        self.home=Path(home)
        (self.home/'config').mkdir(parents=True,exist_ok=True,mode=0o700)
    def path(self,workspace,scope):
        if scope=='global':return self.home/'config/settings.yaml'
        if scope not in {'project','local'}:raise ValueError('Choose global, project, or local scope')
        return Path(workspace).expanduser().resolve()/'.amplifier-unified'/('settings.local.yaml' if scope=='local' else 'settings.yaml')
    def read(self,workspace,scope='global'):
        with FileLock(str(self.home/'config/.settings.lock')):
            return copy.deepcopy(read_yaml(self.path(workspace,scope)))
    def update(self,workspace,scope,mutator):
        with FileLock(str(self.home/'config/.settings.lock')):
            path=self.path(workspace,scope);value=read_yaml(path)
            result=mutator(value)
            if result is not None:value=result
            write_private(path,yaml.safe_dump(value,sort_keys=False))
            return copy.deepcopy(value)
