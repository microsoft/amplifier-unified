"""Bounded, held-only retention proof. Never executes or hydrates native history."""
KEYS=('fenceId','commandId','purpose','instanceId','dataScope')
def selected(intake,args):
    context=args.get('context');fence=intake.fence
    if not isinstance(context,dict) or context.get('purpose')!='retention-hide' or not fence or any(fence.get(k)!=context.get(k) for k in KEYS) or fence.get('state','held')!='held' or fence.get('phase','held')!='held':
        raise ValueError('Exact held retention intake required')
    sessions=args.get('sessions');limit=args.get('limit',101)
    if type(limit) is not int or not 1<=limit<=101 or not isinstance(sessions,list) or not 1<=len(sessions)<=limit or len(set(sessions))!=len(sessions) or any(not isinstance(s,str) or not s.startswith('ahp-session:/') or len(s)>8192 or any(ord(c)<32 for c in s) for s in sessions):
        raise ValueError('At most101 distinct explicit conversations required')
    return sessions

def result(sessions,check,omissions=()):
    return {'coverage':'partial' if omissions else 'complete','protected':[{'session':s,'reasons':reasons} for s in sessions if (reasons:=sorted(set(check(s))))],'omissions':list(omissions)}

def exists(db,sql,args=()):return db.execute(sql,args).fetchone() is not None
