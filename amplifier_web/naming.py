"""App-owned display names, separate from the worker's transcript checkpoints."""
import json
from pathlib import Path

FIELDS=('name','description','name_generated_at','description_updated_at','name_source','naming_completed_inputs')
PLACEHOLDERS={'New conversation','A new conversation','Untitled conversation'}


def automatic(session):
    if session.get('titleSource') == 'native' and session.get('nativeNameSource') == 'manual':
        return False
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


def persist(home,session, *, shared_rename=False):
    from .host.storage import SessionStore
    if session.get('nativeProject'):
        from .automatic_history import directory as native_directory
        directory = native_directory(session)
    else:
        directory=SessionStore.for_app(home, session.get('workspace') or Path.cwd()).directory(session.get('runtimeSessionId') or session['id'])
    directory.mkdir(parents=True,exist_ok=True,mode=0o700)
    value=read(directory)
    value.update(name_source=session.get('titleSource','manual'))
    if value['name_source'] in {'manual','generated'}:
        value['name']=session['title']
    if session.get('description'):value['description']=session['description']
    SessionStore._atomic(directory/'naming.json',json.dumps(value))
    if shared_rename and any((directory/name).is_file() for name in ('metadata.json','metadata.json.backup')):
        # Match CLI's explicit metadata rename; opening history never calls this.
        from amplifier_foundation.session.history import SessionHistoryStore
        history=SessionHistoryStore(directory)
        metadata=history.load_metadata()
        metadata.update({key:value[key] for key in ('name','name_source') if key in value})
        history.save_metadata(metadata)
