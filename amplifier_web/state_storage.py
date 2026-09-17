"""Keep one configuration per session; load bulky provenance only on demand."""
import hashlib
import json


def normalize_state(state, db):
    from .canvas_library import remember
    remember(state, db)
    legacy=state.pop('sessionConfiguration', {})
    controls=state.get('runtimeControl', {})
    for session in state.get('sessions', []):
        sid=session['id'];runtime=controls.get(sid,{})
        config=session.get('configuration') or legacy.get(sid) or runtime.get('configuration.inspect')
        if not isinstance(config,dict) or 'plan' not in config:
            continue
        provenance=config.get('provenance')
        if isinstance(provenance,dict) and '$resource' not in provenance:
            text=json.dumps(provenance,ensure_ascii=False)
            if len(text)>16000:
                identity=hashlib.sha256(text.encode()).hexdigest()
                db.execute('INSERT OR IGNORE INTO state_resources VALUES (?, ?)',(identity,text))
                config['provenance']={'$resource':identity,'bytes':len(text.encode()),'summary':{key:len(value) if isinstance(value,(list,dict)) else 1 for key,value in provenance.items()}}
        session['configuration']=config
        for op in ['configuration.inspect','configuration.apply','configuration.toggle']:
            result=runtime.get(op)
            if isinstance(result,dict) and ('plan' in result or 'configuration' in result):
                runtime[op]={'configurationSessionId':sid,**{key:result[key] for key in ('requiresRestart','applied','accepted') if key in result}}


def resource(db, identity):
    if not isinstance(identity,str) or len(identity)!=64 or any(c not in '0123456789abcdef' for c in identity):
        raise ValueError('Invalid state resource.')
    row=db.execute('SELECT value FROM state_resources WHERE id=?',(identity,)).fetchone()
    if not row:
        raise ValueError('State resource is unavailable.')
    return json.loads(row[0])
