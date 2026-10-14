"""Bounded, held-only retention proof. Never executes or hydrates native history."""
KEYS=('fenceId','commandId','purpose','instanceId','dataScope')
def selected(intake,args,*,purpose='retention-hide'):
    context=args.get('context');fence=intake.fence
    if not isinstance(context,dict) or context.get('purpose')!=purpose or not fence or any(fence.get(k)!=context.get(k) for k in KEYS) or fence.get('state','held')!='held' or fence.get('phase','held')!='held':
        raise ValueError('Exact held retention intake required')
    sessions=args.get('sessions');limit=args.get('limit',101)
    if type(limit) is not int or not 1<=limit<=101 or not isinstance(sessions,list) or not 1<=len(sessions)<=limit or len(set(sessions))!=len(sessions) or any(not isinstance(s,str) or not s.startswith('ahp-session:/') or len(s)>8192 or any(ord(c)<32 for c in s) for s in sessions):
        raise ValueError('At most101 distinct explicit conversations required')
    return sessions

def result(sessions,check,omissions=()):
    return {'coverage':'partial' if omissions else 'complete','protected':[{'session':s,'reasons':reasons} for s in sessions if (reasons:=sorted(set(check(s))))],'omissions':list(omissions)}

def exists(db,sql,args=()):return db.execute(sql,args).fetchone() is not None

def managed_selected(intake,args):
    import re
    a=args.get('allocation')
    if not isinstance(a,dict) or set(a)!={'allocationId','executionDirectory','allocationHash','treeHash','entryCount','bytes'} or not isinstance(a.get('allocationId'),str) or not re.fullmatch(r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}',a['allocationId']) or any(not isinstance(a.get(k),str) or not re.fullmatch(r'[0-9a-f]{64}',a[k]) for k in ('allocationHash','treeHash')) or any(type(a.get(k)) is not int or not 0<=a[k]<=9007199254740991 for k in ('entryCount','bytes')):
        raise ValueError('Exact reviewed managed allocation required')
    p=a.get('executionDirectory')
    if not isinstance(p,str) or not p.startswith('/') or p=='/' or len(p)>8192 or any(ord(c)<32 or c=='\\' for c in p) or any(v in ('','.','..') for v in p.split('/')[1:]):raise ValueError('Canonical reviewed allocation directory required')
    return selected(intake,args,purpose='managed-files-disposal')

def add_protection(base,sessions,reasons):
    rows={r['session']:set(r['reasons']) for r in base['protected']}
    for session in sessions:
        extra=reasons(session)
        if extra:rows.setdefault(session,set()).update(extra)
    return {**base,'protected':[{'session':s,'reasons':sorted(v)} for s,v in rows.items()]}
