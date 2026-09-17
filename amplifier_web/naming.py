"""App-owned display names, separate from the worker's transcript checkpoints."""
import json
from pathlib import Path

FIELDS=('name','description','name_generated_at','description_updated_at','name_source','naming_completed_inputs')
PLACEHOLDERS={'New conversation','A new conversation','Untitled conversation'}


def automatic(session):
    if 'titleSource' in session:
        return session['titleSource']!='manual'
    first=next((m.get('text','') for m in session.get('messages',[]) if m.get('role')=='user'),'')
    return session.get('title') in PLACEHOLDERS or (first and session.get('title')==first[:64])


def read(directory):
    try:
        value=json.loads((Path(directory)/'naming.json').read_text())
        return {k:v for k,v in value.items() if k in FIELDS} if isinstance(value,dict) else {}
    except (OSError,ValueError):
        return {}


def persist(home,session):
    from .host.storage import SessionStore
    directory=SessionStore(Path(home)/'sessions').directory(session.get('runtimeSessionId') or session['id'])
    directory.mkdir(parents=True,exist_ok=True,mode=0o700)
    value=read(directory)
    value.update(name_source=session.get('titleSource','manual'))
    if value['name_source'] in {'manual','generated'}:
        value['name']=session['title']
    if session.get('description'):value['description']=session['description']
    SessionStore._atomic(directory/'naming.json',json.dumps(value))
