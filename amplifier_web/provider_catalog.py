"""Private, process-local provider catalogs with per-configuration single flight."""
import asyncio
import copy
import hashlib
import json


def fingerprint(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,default=str).encode()).hexdigest()


class ProviderCatalog:
    def __init__(self):
        self.entries={}
        self.pending={}

    async def get(self,key,loader,refresh=False):
        if not refresh and key in self.entries:
            result,error=self.entries[key]
            if error:raise ValueError(error)
            return copy.deepcopy(result)
        if key not in self.pending:
            async def load():
                try:
                    result=await loader()
                    self.entries[key]=(copy.deepcopy(result),None)
                    return result
                except Exception as exc:
                    self.entries[key]=(None,str(exc))
                    raise
                finally:self.pending.pop(key,None)
            self.pending[key]=asyncio.create_task(load())
        return copy.deepcopy(await asyncio.shield(self.pending[key]))

    async def close(self):
        tasks=list(self.pending.values())
        for task in tasks:task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
