"""Explicit user grant and controlled current-root transport for host fixtures.

This does not mount a native runtime or prove a provider turn. Native root and
child authority are qualified separately by test_child_app_authority.py.
Negative/forged-principal tests must use their original untrusted envelopes.
"""


async def authorized_admin(app, sid, *, effects, scopes):
    source = app._session(sid)
    generation = 'controlled-admin-generation:' + sid
    source['collaborationGeneration'] = {'id':generation,'inputIds':['controlled-human-input'],'terminal':False}
    await app.dispatch('capability.grant', {'sessionId':sid,'effects':effects,'scopes':scopes,
        'reason':'Explicit user authorization for the tested administration operation'})

    async def bridge(operation, envelope):
        source = app._session(sid)
        native = source.get('runtimeSessionId') or source.get('nativeIdentity') or sid
        return await app.app_bridge(operation,{**envelope,'_runtimeSessionId':native,
            '_generationId':generation,'_inputBindings':[{'inputId':'controlled-human-input','clientId':None}]},sid)
    return bridge
