"""Historical model choices owned by an actual foreground input, separate from prompts."""
from hashlib import sha256


def binding_proof(input_id, key):
    if isinstance(key, str) and key and isinstance(input_id, str) and input_id:
        return sha256((input_id + '\0' + key).encode()).hexdigest()


def capture(coordinator, instance, model, input_id):
    """Only matching, mounted settings can attest to an explicit or inherited effort."""
    current = coordinator.get_capability('web.context_transition.selection')
    state = current() if callable(current) else None
    if not isinstance(state, dict) or 'selection' not in state:
        return None
    pinned = state['selection']
    choice = {'instance': instance, 'model': model}
    if pinned is not None:
        if not isinstance(pinned, dict) or any(pinned.get(k) != v for k, v in choice.items()):
            return None
        if pinned.get('effort'):
            choice['effort'] = pinned['effort']
    catalog = coordinator.get_capability('web.provider_catalog') or {}
    mounted = catalog.get('connectionKeys', {}).get(instance)
    if pinned and state.get('connection') and state['connection'] != mounted:
        return None
    proof = binding_proof(input_id, mounted)
    return {'selection': choice, 'pinned': pinned is not None,
            **({'bindingProof': proof} if proof else {})}


def public_receipt(value, model):
    """Allowlist only the local worker's bounded choice and opaque input-scoped proof."""
    if not isinstance(value, dict) or type(value.get('pinned')) is not bool:
        return None
    choice = value.get('selection')
    if (not isinstance(choice, dict) or choice.get('model') != model or
            any(not isinstance(choice.get(k), str) or not 0 < len(choice[k]) <= n
                for k, n in [('instance', 200), ('model', 500)]) or
            'effort' in choice and not (isinstance(choice['effort'], str) and 0 < len(choice['effort']) <= 100)):
        return None
    result = {'selection': {k: choice[k] for k in ('instance', 'model', 'effort') if k in choice},
              'pinned': value['pinned']}
    proof = value.get('bindingProof')
    if isinstance(proof, str) and len(proof) == 64 and all(c in '0123456789abcdef' for c in proof):
        result['bindingProof'] = proof
    return result


def record(session, receipt, configuration_revision=None):
    """Join the first foreground request to its exact admitted native turn."""
    # A hook-only request can precede a wrapped call that carries real settings.
    # Its completion, however, proves that an otherwise unverified turn occurred.
    observed = (receipt.get('phase') == 'running' and 'turnModelSettings' in receipt
                or receipt.get('phase') == 'completed')
    if (not observed or receipt.get('kind') != 'llm' or receipt.get('purpose') or receipt.get('lifecycle') == 'background'
            or receipt.get('sessionId') != session['id'] or receipt.get('rootSessionId') != session['id']):
        return
    input_id = receipt.get('admissionInputId')
    owner, generation = receipt.get('runtimeOwnerId'), receipt.get('generationId')
    active = session.get('collaborationGeneration') or {}
    if (not input_id or not owner or not generation or generation != active.get('id') or active.get('terminal')
            or active.get('interruptionRevision') != session.get('interruptionRevision', 0)
            or session.get('status') in {'stopping', 'stopped'}):
        return
    from .execution_working_set import exact
    turn = exact(session, 'turns', {'id': input_id})
    if (not turn or turn.get('runtimeOwnerId') != owner or turn.get('generationId') != generation
            or turn.get('modelSettingsGeneration') == generation):
        return
    from .next_message_configuration import target
    turn['modelSettingsGeneration'] = generation
    previous = session.pop('previousTurnSettings', None)
    session.pop('_previousTurnBinding', None)
    captured = receipt.get('turnModelSettings') or {}
    if not captured:
        return
    selection = captured['selection']
    # Older usage observations lack a turn/attempt anchor. A later failed
    # dispatch can have changed models since the last *completed* usage sample.
    prior = previous and previous['selection']
    if prior and selection != prior:
        change = {'previous': prior, 'current': selection}
        hint = session.get('nextMessageImpact') or {}
        if hint.get('source') == {k: prior[k] for k in ('instance','model')}:
            if selection['instance'] in hint.get('coldConnections', []):
                change['impact'] = 'portable-history' if hint.get('portableHistoryTokens') else 'cold-request'
            elif hint.get('coldModels') and selection['instance'] == prior['instance'] and selection['model'] != prior['model']:
                change['impact'] = 'cold-request'
        turn['modelChange'] = change
    latest = session['previousTurnSettings'] = {'selection': selection}
    witness = captured.get('bindingProof')
    if witness and session.get('bundle'):
        latest.update(inputId=input_id, generationId=generation, bundle=session['bundle'],
                      configurationRevision=configuration_revision)
        session['_previousTurnBinding'] = {'proof': witness, 'pinned': captured['pinned'],
            'sessionId': session['id'], 'target': target(session),
            'appliedRevision': session.get('appliedConfigurationRevision', 0)}


def avoidable(service, session):
    """Show a shortcut only when the prior measured route remains reusable."""
    from .next_message_configuration import target
    from .provider_catalog import configured_binding
    previous = session.get('previousTurnSettings') or {}
    selected = session.get('pendingModelSelection') or {}
    old = previous.get('selection') or {}
    hint = session.get('nextMessageImpact') or {}
    private = session.get('_previousTurnBinding') or {}
    if (not previous.get('inputId') or not selected or not hint.get('source') or
            previous.get('configurationRevision') != service.state.get('configurationRevision', 0) or
            previous.get('restorePending') or session.get('pendingBundleSelection') or
            session.get('bundle') != previous.get('bundle') or private.get('target') != target(session) or
            private.get('appliedRevision') != session.get('appliedConfigurationRevision', 0) or
            any(session.get(k) for k in ('configurationBusy', 'configurationPending','pendingConfiguration','bundleReplacement')) or
            hint['source'] != {k: old.get(k) for k in ('model', 'instance')}):
        return False
    cold = (selected.get('instance') in hint.get('coldConnections', []) or
            selected.get('instance') == old['instance'] and selected.get('model') != old['model'] and hint.get('coldModels'))
    if not cold:
        return False
    try:
        binding = configured_binding(service.data_dir, session['workspace'], old['instance'],
                                     global_only=session.get('location', {}).get('kind') == 'managed')
    except (ValueError, OSError):
        return False
    return bool(private.get('proof') and binding_proof(previous['inputId'], binding) == private['proof'])


def restore(service, session, input_id, generation, authority, *, keep_independent_edits=False):
    """Undo only a pending model or bundle edit, with the normal configuration lock held."""
    from .errors import AppError
    from .next_message_configuration import pending_input, target
    from .provider_catalog import configured_binding
    previous = session.get('previousTurnSettings') or {}
    private = session.get('_previousTurnBinding') or {}
    if (previous.get('inputId') != input_id or previous.get('generationId') != generation or
            private.get('sessionId') != session['id'] or private.get('target') != target(session) or
            not (session.get('pendingModelSelection') or session.get('pendingBundleSelection'))):
        raise AppError('The previous turn or pending settings changed. Reopen the settings panel.', 409)
    choice = previous['selection']
    binding = configured_binding(service.data_dir, session['workspace'], choice['instance'],
                                 global_only=session.get('location', {}).get('kind') == 'managed')
    if binding_proof(input_id, binding) != private['proof']:
        raise AppError('The previous connection changed or is unavailable. Choose its current settings explicitly.', 409)
    if previous.get('restorePending'):
        return {'revision': session['nextMessageRevision'], 'status': session['nextMessageStatus'],
                'applies': 'next-message', 'mounted': False}
    # Saving a picker choice alone never mounted it. A frozen input can still mount
    # its captured choice later, so give the next input an explicit corrective plan.
    queued = (session.get('appliedConfigurationRevision', 0) != private['appliedRevision']
              or session.get('bundle') != previous['bundle'] or pending_input(service, session))
    bundle_pending = bool(session.pop('pendingBundleSelection', None))
    session.pop('pendingModelSelection', None)
    session.pop('pendingChoiceAuthority', None)
    session.pop('bundlePreview', None)
    retained = private.get('retainedSelection') if keep_independent_edits else None
    if retained:
        choice = retained
    if private['pinned'] or retained:
        session['selection'] = choice.copy()
        session['modelConnectionKey'] = binding
        if queued or retained:
            session['pendingModelSelection'] = choice.copy()
            if authority and 'operatorConfiguration' in authority:
                session['pendingChoiceAuthority'] = authority
    else:
        session['selection'] = None
        session.pop('modelConnectionKey', None)
    if queued and (bundle_pending or not (private['pinned'] or retained) or previous['bundle'] != session.get('bundle')):
        session['pendingBundleSelection'] = {'bundle': previous['bundle'], 'resetModel': not bool(private['pinned'] or retained),
                                            'authority': authority, 'target': target(session)}
    if queued or retained:
        previous['restorePending'] = True
    session['nextMessageRevision'] = session.get('nextMessageRevision', 0) + 1
    session['nextMessageStatus'] = 'saved' if queued or retained else 'applied'
    session.pop('nextMessageError', None)
    session.pop('nextMessageFailure', None)
    return {'revision': session['nextMessageRevision'], 'status': session['nextMessageStatus'],
            'applies': 'next-message', 'mounted': False}
