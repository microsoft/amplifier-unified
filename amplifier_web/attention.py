"""Shared, acknowledgeable attention state derived from currently actionable facts."""
import hashlib
import json


def snapshot(state):
    items=[]
    read=state.get('attentionRead',{})
    def add(key,title,section,page,detail='',version=None):
        fingerprint=hashlib.sha256(json.dumps([key,title,detail,version],sort_keys=True,default=str).encode()).hexdigest()[:24]
        items.append({'id':key,'title':title,'detail':detail,'section':section,'page':page,'fingerprint':fingerprint,'read':read.get(key)==fingerprint})
    updates=state.get('updates',{})
    for row in updates.get('items',[]):
        if row.get('status') in {'update','check_failed','local_changes'}:
            status=row['status'];label={'update':'Update available','check_failed':'Could not check','local_changes':'Local edits preserved'}[status]
            add('source:'+row['id'],label+' · '+row['label'],'maintenance','updates',row.get('detail',''),[status,row.get('latest'),row.get('current')])
    if updates.get('error'):add('updates:error','Update needs attention','maintenance','updates',updates['error'])
    if updates.get('pendingApp') or updates.get('pendingRelease'):add('updates:pending','Update ready; waiting for idle','maintenance','updates',version=updates.get('pendingRelease') or updates.get('pendingApp'))
    routes={'providers':('setup','providers'),'routing':('setup','routing'),'configuration':('capabilities','loaded-modules'),'bundle':('capabilities','add-bundles'),'bundles':('capabilities','app-bundles'),'modules':('capabilities','registries'),'sources':('capabilities','registries'),'history':('maintenance','history'),'permissions':('maintenance','permissions'),'notifications':('maintenance','notifications'),'maintenance':('maintenance','repair')}
    for action,op in state.get('actionStatus',{}).items():
        if op.get('phase') not in {'error','failed'} or not op.get('error'):continue
        section,page=routes.get(action.split('.')[0],('maintenance','repair'))
        add('action:'+action,'Action needs attention',section,page,op['error'],op.get('commandId'))
    for op in state.get('smartTools',{}).get('operations',[]):
        if op.get('status') in {'failed','interrupted'}:
            add('smart-tool:'+op['id'],'Smart Tool needs attention','capabilities','smart-tools',op.get('error','The tool did not finish.'),op.get('updatedAt'))
    if state.get('notificationError'):add('notifications:error','Notification delivery failed','maintenance','notifications',state['notificationError'])
    for session in state.get('sessions',[]):
        if session.get('error'):add('session:'+session['id'],'Conversation needs attention','setup','conversation',session['error'])
        for approval in session.get('approvals',[]):
            if approval.get('status') in {None,'pending'}:add('approval:'+approval['id'],'Approval requested · '+session.get('title','Conversation'),'setup','conversation',approval.get('title') or approval.get('tool',''))
    unread=[item for item in items if not item['read']]
    return {'items':items,'unread':len(unread),'sections':{key:sum(i['section']==key or (key=='setup' and i['page']=='loaded-modules') for i in unread) for key in {'setup','capabilities','maintenance'}},'pages':{key:sum(i['page']==key for i in unread) for key in {i['page'] for i in items}}}
