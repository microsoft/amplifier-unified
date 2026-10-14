"""Policy/redaction adapted from Amplifier Unified diagnostics (MIT).

No capture scan or native source ownership is imported with these pure helpers.
"""
import copy,hashlib,json,math,os,re,uuid
from urllib.parse import urlsplit

STREAMS = {
    'app': 'App actions', 'sessions': 'Session lifecycle', 'workers': 'Worker activity',
    'tools': 'Tool activity', 'usage': 'Models, tokens and cost', 'canvas': 'Canvas activity',
    'smartTools': 'Smart Tool activity', 'updates': 'Update diagnostics',
    'conversation': 'Conversation text (content)',
}

METADATA = [key for key in STREAMS if key != 'conversation']

DEFAULT = {'enabled': False, 'streams': METADATA, 'retentionDays': 30, 'maxRecords': 25000, 'destinations': [], 'providerRequests': False}

META_KEYS = {'id','sessionId','rootSessionId','parentId','turnId','inputId','messageId','toolCallId','callId',
             'tool_name','tool_call_id','input_tokens','output_tokens','total_tokens','cache_read_input_tokens','cache_creation_input_tokens','cost_usd',
             'commandId','attemptId','action','origin','kind','phase','status','role','via','tool','provider','model',
             'startedAt','endedAt','at','durationMs','exitCode','stdoutBytes','stderrBytes','errorType','timedOut',
             'expectedVersion','observedVersion','revision','probe','usage','costUsd','costType','inputTokens',
             'outputTokens','totalTokens','cacheReadTokens','cacheWriteTokens','serverId','operationId','artifactId',
             'ok','isolated','packageInEnvironment','frontendPresent','loginAvailable','standalone','providersPresent',
             'cliAbsent','appSessionId','pythonVersion','version','stage','size','count','errorCode','runtimeSessionId'}

SECRET_KEY = re.compile(r'(?i)(api.?key|password|secret|authorization|cookie|credential|access.?token|refresh.?token)')

def metadata_fields(data):
    selected = {key: value for key, value in data.items() if key in META_KEYS}
    if data.get('kind') == 'llm' and data.get('label') == 'Context compaction':
        selected['label'] = 'Context compaction'
    return selected

def clean(value, depth=0):
    """Best-effort content redaction; explicit content opt-in is still sensitive."""
    if depth > 12: return '[depth limit]'
    if isinstance(value, dict):
        return {str(k)[:100]: '[REDACTED]' if SECRET_KEY.search(str(k)) else clean(v,depth+1) for k,v in list(value.items())[:100]}
    if isinstance(value, list): return [clean(v,depth+1) for v in value[:100]]
    if isinstance(value,str):
        text=value[:16000]
        text=re.sub(r'(?i)(bearer\s+)[^\s\"\']+',r'\1[REDACTED]',text)
        text=re.sub(r'(?i)(https?://)[^\s/@]+:[^\s/@]+@',r'\1[REDACTED]@',text)
        for name,secret in os.environ.items():
            if SECRET_KEY.search(name) and len(secret)>=8: text=text.replace(secret,'[REDACTED]')
        return text
    if isinstance(value,float) and not math.isfinite(value):return None
    if isinstance(value,(int,float,bool)) or value is None: return value
    return '[unsupported]'

def validate_config(value):
    if not isinstance(value,dict) or set(value)-set(DEFAULT): raise ValueError('Unknown diagnostics setting.')
    cfg={**copy.deepcopy(DEFAULT),**copy.deepcopy(value)}
    if type(cfg['enabled']) is not bool: raise ValueError('Capture enabled must be true or false.')
    if type(cfg['providerRequests']) is not bool: raise ValueError('Provider request recording must be true or false.')
    for key,minimum,maximum in [('retentionDays',1,365),('maxRecords',100,100000)]:
        if type(cfg[key]) is not int or not minimum<=cfg[key]<=maximum: raise ValueError(f'{key} must be between {minimum} and {maximum}.')
    def streams(items):
        if not isinstance(items,list) or any(s not in STREAMS for s in items): raise ValueError('Choose known data streams.')
        return list(dict.fromkeys(items))
    cfg['streams']=streams(cfg['streams'])
    if cfg['providerRequests']:raise ValueError('Provider request capture requires the native capture owner and is not available through this metadata owner.')
    if not isinstance(cfg['destinations'],list) or len(cfg['destinations'])>10: raise ValueError('Use at most ten destinations.')
    seen=set()
    for dest in cfg['destinations']:
        allowed={'id','name','url','enabled','streams','workspace','workspacePattern','includePaths','authMode','apiKeyEnv','authResource'}
        if not isinstance(dest,dict) or set(dest)-allowed: raise ValueError('Unknown destination setting.')
        dest.setdefault('id',str(uuid.uuid4()));dest.setdefault('name','Context Intelligence')
        dest.setdefault('enabled',False);dest.setdefault('streams',[])
        dest.setdefault('workspace','amplifier-unified');dest.setdefault('workspacePattern','*')
        dest.setdefault('includePaths',False);dest.setdefault('authMode','static')
        dest.setdefault('apiKeyEnv','AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY');dest.setdefault('authResource','')
        for key in ('id','name','url','workspace','workspacePattern','apiKeyEnv','authResource'):
            if not isinstance(dest.get(key),str) or len(dest[key])>2000: raise ValueError(f'Invalid destination {key}.')
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',dest['id']) or dest['id'] in seen: raise ValueError('Destination IDs must be unique.')
        seen.add(dest['id'])
        if not dest['name'].strip() or not dest['workspace'].strip(): raise ValueError('Give each destination a name and workspace label.')
        parsed=urlsplit(dest['url'])
        try: parsed.port
        except ValueError: raise ValueError('Invalid server port.') from None
        if parsed.scheme not in {'http','https'} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('Use an HTTP(S) server URL without credentials, query or fragment.')
        if parsed.scheme=='http' and parsed.hostname not in {'localhost','127.0.0.1','::1'}:
            raise ValueError('Use HTTPS for remote servers; HTTP is allowed for localhost.')
        dest['url']=dest['url'].rstrip('/')
        if dest['authMode'] not in {'static','entra'}: raise ValueError('Choose API key or Microsoft Entra authentication.')
        if dest['authMode']=='static' and not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',dest['apiKeyEnv']): raise ValueError('Enter an environment variable name, not an API key.')
        if dest['authMode']=='entra' and not dest['authResource'].strip(): raise ValueError('Entra authentication requires a resource URI.')
        if type(dest['enabled']) is not bool or type(dest['includePaths']) is not bool: raise ValueError('Destination switches must be true or false.')
        dest['streams']=streams(dest['streams'])
        if set(dest['streams'])-set(cfg['streams']): raise ValueError('Enable local capture for every stream selected by a destination.')
    return cfg

def route_revision(dest):
    return hashlib.sha256(json.dumps(dest,sort_keys=True).encode()).hexdigest()
