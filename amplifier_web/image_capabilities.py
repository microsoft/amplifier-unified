"""Resolve image support from the selected provider's public model catalog."""
import asyncio
import inspect
import time

from .provider_probe import model_list_info

IMAGE_TAGS = {'vision', 'image', 'images', 'multimodal'}


async def _model_catalog(provider, loader):
    # Recommendations may omit a valid saved model. Ask the same provider for
    # its advertised full inventory without altering its picker configuration.
    kwargs = {}
    try:
        # A legacy subclass may inherit the hint but override the method.
        inspect.signature(loader).bind(filters={})
        info = await model_list_info(provider)
        if info and info.get("filters_keyword") == "filters" and isinstance(info.get("unfiltered"), dict):
            kwargs["filters"] = info["unfiltered"]
    except (TypeError, ValueError):
        pass
    rows = loader(**kwargs)
    return await rows if inspect.isawaitable(rows) else rows


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
            rows = await asyncio.wait_for(_model_catalog(provider, loader), 3)
            match = next((row for row in rows[:10000] if read(row,'id')==model),None)
            result = bool(match and IMAGE_TAGS.intersection(read(match,'capabilities',[]) or []))
            self.cache = (self.cache+[(owner,model,time.monotonic()+300,result)])[-16:]
            return result
        except Exception:
            return False
