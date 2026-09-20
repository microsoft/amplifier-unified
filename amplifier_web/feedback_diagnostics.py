"""Allowlisted reproduction facts. Never serialize devices, drafts or errors."""
import hashlib
import json
import platform
from pathlib import Path
from . import __version__

DEVICE_FIELDS = {
    'frontendVersion': {'type':'string','pattern':r'^(unknown|dev|[0-9]+\.[0-9]+\.[0-9]+(?:[a-zA-Z0-9.+-]*))$','maxLength':60},
    'frontendBuild': {'type':'string','pattern':r'^(unknown|dev|[a-f0-9]{12,64})$'},
    'browser': {'enum':['Chrome','Edge','Firefox','Safari','Other']},
    'browserVersion': {'type':'string','pattern':r'^[0-9.]{0,40}$'},
    'deviceOS': {'enum':['Windows','macOS','Linux','Android','iOS','Other']},
    'width': {'type':'integer','minimum':0,'maximum':32768},
    'height': {'type':'integer','minimum':0,'maximum':32768},
    'pixelRatio': {'type':'number','minimum':0,'maximum':16},
    'colorPreference': {'enum':['dark','light']},
    'appearance': {'enum':['dark','light','system']},
    'resolvedAppearance': {'enum':['dark','light']},
    'standalone': {'type':'boolean'}, 'secureContext': {'type':'boolean'},
    'online': {'type':'boolean'}, 'serviceWorkerControlled': {'type':'boolean'},
    'reducedMotion': {'type':'boolean'}, 'visible': {'type':'boolean'},
    'pageAgeSeconds': {'type':'integer','minimum':0,'maximum':31536000},
    'pendingActions': {'type':'integer','minimum':0,'maximum':100000},
    'oldestPendingMs': {'type':'integer','minimum':0,'maximum':31536000000},
}
DEVICE_SCHEMA={'type':'object','properties':DEVICE_FIELDS,'additionalProperties':False}

def build_facts():
    try:
        build=json.loads((Path(__file__).parent/'static/build.json').read_text())
    except (OSError,ValueError):
        build={}
    return {'appVersion':__version__,'osFamily':platform.system(), 'pythonVersion':platform.python_version(),
        'packagedFrontendVersion':build.get('version','unknown'),'packagedFrontendBuild':build.get('id','unknown')}

def snapshot(state, device=None):
    session=next((row for row in state.get('sessions',[]) if row.get('id')==state.get('selectedSessionId')), {})
    view=state.get('view',{});workers=session.get('workers',[])
    statuses={}
    for row in workers:
        status=row.get('status')
        key=status if status in {'idle','running','working','starting','queued','completed','cancelled','error','failed','stopped','interrupted'} else 'other'
        statuses[key]=statuses.get(key,0)+1
    result={**build_facts(),'stateRevision':state.get('revision'),
        'library':{'conversations':len(state.get('sessions',[])),'workspaces':len(state.get('workspaces',[])),
            'availableWorkspaces':sum(w.get('available') is True for w in state.get('workspaces',[])),
            'scope':'all' if view.get('navChatScope')=='all' else 'workspace','filterActive':bool(view.get('navFilter')),
            'page':state.get('chatNavigation',{}).get('index',0)},
        'conversation':{'status':session.get('status') if session.get('status') in {'idle','working','running','starting','stopping','stopped','error','ready'} else 'other',
            'kind':'worker' if session.get('sessionKind')=='worker' or session.get('nativeParentId') else 'root',
            'historyLoaded':session.get('historyLoaded'),'historyLoading':bool(session.get('historyLoading')),
            'historyError':bool(session.get('historyError')),'runtimeError':bool(session.get('error')),
            'workspaceAvailable':session.get('workspaceAvailable'), 'messages':len(session.get('messages',[])),
            'retainedMessages':session.get('sharedHistoryTotal'),'toolNodes':len(session.get('execution',{}).get('nodes',[])),
            'workerStatuses':statuses,'pendingApprovals':sum(r.get('status') in {None,'pending'} for r in session.get('approvals',[]))},
        'presentation':{'appearance':view.get('scheme') if view.get('scheme') in {'dark','light','system'} else 'system',
            'skinFingerprint':hashlib.sha256(str(state.get('theme',{}).get('css','')).encode()).hexdigest()[:16],
            'canvasKind':state.get('canvas',{}).get('kind') if state.get('canvas',{}).get('kind') in {'html','markdown','text','image','code','json','jsonl','mermaid','dot','mcp-app','browser','a2ui','babylon'} else None},
        'connectedViews':sum(row.get('online') is True for row in state.get('devices',{}).values())}
    if device: result['device']=device
    return result

def markdown(facts):
    return '\n\n### Reproduction diagnostics\n\n```json\n'+json.dumps(facts,indent=2,sort_keys=True)+'\n```'
