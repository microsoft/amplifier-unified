"""Build sandbox documents with explicitly bundled browser libraries."""
from functools import lru_cache
from pathlib import Path
import re


@lru_cache(maxsize=1)
def babylon_script():
    # Escape HTML parser terminators without changing JavaScript string values.
    source=(Path(__file__).parent/'static/vendor/babylon.js').read_text()
    return re.sub(r'</script',r'<\\/script',source,flags=re.IGNORECASE)


def raw_source(canvas, db=None):
    if canvas.get('contentResource'):
        from .state_storage import resource
        return resource(db,canvas['contentResource']['$resource']).get('content','')
    return canvas.get('content','')


def canvas_source(canvas, db=None):
    content=raw_source(canvas,db)
    if canvas.get('kind')=='babylon':
        return '<script data-amplifier-library="babylon">'+babylon_script()+'</script>'+content
    return content
