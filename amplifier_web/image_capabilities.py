"""Resolve image support from the selected provider's public model catalog."""
import asyncio
import inspect
import time

IMAGE_TAGS = {'vision', 'image', 'images', 'multimodal'}


class ImageCapabilities:
    def __init__(self):
        self.cache = []

    async def supports(self, request, provider):
        try:
            info = provider.get_info()
            if inspect.isawaitable(info):
                info = await asyncio.wait_for(info, 3)
            read = lambda row,key,default=None: row.get(key,default) if isinstance(row,dict) else getattr(row,key,default)
            selection = getattr(provider, 'selection', None)
            model = (selection.get('model') if isinstance(selection,dict) else None) or request.model or (read(info,'defaults',{}) or {}).get('model')
            loader = getattr(provider, 'list_models', None)
            if not model or not callable(loader):
                return bool(IMAGE_TAGS.intersection(read(info,'capabilities',[]) or []))
            owner = provider
            for _ in range(8):
                previous = getattr(owner, 'original', None)
                if previous is None or previous is owner:break
                owner = previous
            for cached_owner,cached_model,until,value in self.cache:
                if cached_owner is owner and cached_model == model and until > time.monotonic():
                    return value
            rows = loader()
            if inspect.isawaitable(rows):
                rows = await asyncio.wait_for(rows, 3)
            match = next((row for row in rows[:10000] if read(row,'id')==model),None)
            result = bool(match and IMAGE_TAGS.intersection(read(match,'capabilities',[]) or []))
            self.cache = (self.cache+[(owner,model,time.monotonic()+300,result)])[-16:]
            return result
        except Exception:
            return False
