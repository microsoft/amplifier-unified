"""Keep legacy deep links working without listing chat controls in global Settings."""
CHAT_PAGES = {'conversation': 'session-details', 'runtime': 'chat-advanced',
              'loaded-modules': 'chat-models', 'share-bundle': 'chat-models', 'recall': 'chat-memory'}

def route_legacy(state, patch):
    if patch.get('panel', state.get('view', {}).get('panel')) not in {'settings', 'appearance'}:
        return patch
    page = next((p for p in patch.get('settingsExpanded', []) if p in CHAT_PAGES or p in {'outputs', 'publishing'}), None)
    if page in CHAT_PAGES:
        return {**patch, 'panel': CHAT_PAGES[page], 'settingsExpanded': [], 'settingsCanvas': False}
    if page in {'outputs', 'publishing'}:
        return {**patch, 'panel': None, 'settingsExpanded': [], 'settingsCanvas': False,
                'canvasDraft': {**state.get('view', {}).get('canvasDraft', {}), 'library': True, 'delivery': page}}
    return patch

GLOBAL_PAGES = {
    'ai-connections':'AI connections', 'appearance':'Appearance', 'smart-tools':'Smart Tools',
    'workspaces':'Workspace defaults', 'workspace-starters':'Workspace starters', 'voice':'Voice',
    'notifications':'Notifications', 'privacy':'Privacy & files', 'permissions':'Global file access',
    'desktop':'Host screen & computer setup', 'diagnostics':'App diagnostic capture', 'history':'Saved work',
    'updates':'Updates', 'advanced':'Advanced', 'providers':'Provider configuration', 'routing':'Model rules',
    'app-bundles':'Configured bundles', 'add-bundles':'Discover bundles', 'defaults':'Conversation defaults',
    'registries':'Module & source registries', 'tool-connections':'Tool connections',
    'custom-appearance':'Custom appearance', 'repair':'Backup & repair', 'reset':'Advanced recovery',
    'ready-conversations':'Ready conversations', 'automation':'Terminal & automation', 'install-app':'Install app'}

def settings_state(state):
    view = state.get('view', {})
    return {'surface':'canvas' if view.get('settingsCanvas') and state.get('canvas', {}).get('open') else 'dialog' if view.get('panel') in {'settings','appearance'} else 'closed',
            'page':next((p for p in view.get('settingsExpanded', []) if p in GLOBAL_PAGES), 'ai-connections'),
            'pages':[{'id':key,'title':value} for key,value in GLOBAL_PAGES.items()],
            'chatPages':CHAT_PAGES, 'setupStep':view.get('setupJourney'),
            **({'setupGuide': 'Ask what the user wants to work on. Offer to finish settings, then an optional tour: file attachments, Canvas outputs, workspaces and pins, plans and Chat overview, dictation or voice, and images. Demonstrate only chosen features. Explain changes and any paid or external action first. Offer to skip to a new chat. Credentials must be entered privately in Settings.'} if view.get('setupJourney') == 'assisted' else {})}
