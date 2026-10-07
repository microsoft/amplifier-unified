"""Browser-only view contracts. Durable and agent state retain complete evidence.

A panel change is a normal scoped view update, so its next snapshot atomically
brings the requested data. Closing it retires that data from the browser and
its stream baseline; revisiting reads the current server cache, not the network
provider. No omission here changes persistence or action receipts.
"""


def pick(value, fields):
    return {key: value[key] for key in fields if key in value}


def settings_page(view):
    if view.get('panel') not in {'settings', 'appearance'}:
        return None
    if view.get('panel') == 'appearance':
        return 'appearance'
    expanded = view.get('settingsExpanded') or []
    return next(iter(expanded), None) or {'capabilities': 'app-bundles', 'maintenance': 'updates'}.get(view.get('settingsSection'), 'ai-connections')


def project(result):
    view = result.get('view', {})
    panel, page = view.get('panel'), settings_page(view)
    runtime = panel in {'runtime', 'session-details'}
    # Only selected conversation receipts participate in automatic acknowledgement.
    # Full reviewed notices remain available in the Activity and relevant Settings view.
    attention = result.get('attention', {})
    result['attention'] = {**attention,
        'workspaces': {key: value for key, value in attention.get('workspaces', {}).items() if value},
        'items': [item for item in attention.get('items', []) if panel == 'activity'
                  or result.get('selectedSessionId') and item.get('sessionId') == result['selectedSessionId']
                  or page and item.get('page') == page
                  or panel == 'feedback' and item.get('requestId')]}
    updates = result.get('updates', {})
    if page != 'updates':
        result['updates'] = pick(updates, ('phase', 'error', 'detail', 'available', 'appAvailable',
            'pendingApp', 'pendingRelease', 'pendingRestart', 'pendingReplacement', 'installedAt',
            'featureResults', 'adoption'))
        result['updates']['application'] = pick(updates.get('application', {}),
            ('id', 'label', 'kind', 'current', 'latest', 'status', 'canInstall'))
    smart = result.get('smartTools', {})
    if page not in {'smart-tools', 'tool-connections'}:
        result['smartTools'] = {'servers': [pick(row, ('id', 'name', 'status', 'capabilities'))
                                         for row in smart.get('servers', [])]}
    # Composer connections and their cached model capabilities are always ready.
    # Setup operation receipts, matrices and diagnostic metadata are page resources.
    if page not in {'ai-connections', 'overview', 'providers', 'routing', 'voice', 'defaults', 'desktop'}:
        result['setup'] = pick(result.get('setup', {}), ('providers', 'providerCatalogs', 'metadata',
            'providersWorkspace', 'providersRequestedWorkspace', 'providersLocation', 'providersLoadedAt'))
    if not runtime:
        result['runtimeControl'] = {sid: pick(control, ('configuration.catalog', 'configuration.providers'))
                                    for sid, control in result.get('runtimeControl', {}).items()}
        result['sessions'] = [{**{key: value for key, value in row.items()
                                  if key not in {'configuration', 'collaborationMessageAnchors', 'surfaceInputs'}},
                               **({'task': pick(row['task'], ('id', 'status', 'revision', 'appliedRevision', 'objective', 'blockedReason'))}
                                  if row.get('task') else {})}
                              for row in result.get('sessions', [])]
    if page != 'history':
        result['sharedHistory'] = {key: value for key, value in result.get('sharedHistory', {}).items() if key != 'issues'}
    resources = {'registry': {'registries'}, 'bundleDiscovery': {'add-bundles'},
                 'workspaceStarters': {'workspace-starters', 'workspaces'},
                 'diagnostics': {'diagnostics'}, 'maintenance': {'repair', 'reset'},
                 'feedback': set()}
    for key, pages in resources.items():
        if page not in pages and not (key == 'feedback' and panel == 'feedback') and not (key == 'workspaceStarters' and view.get('workSurface') in {'home', 'workspace'}):
            result.pop(key, None)
    # The event journal and past command results are inspected via their existing
    # bounded read APIs, not eagerly replayed into every web client.
    for key in ('events', 'managementResults', 'canvasLibraryMigration', 'defaultBundleMigration',
                'sharedVoiceMigration', 'clientViewsMigrated', 'hiddenNativeSessions'):
        result.pop(key, None)
    if not view.get('locationPicker') and not (view.get('workSurface') == 'workspace' and view.get('workWorkspaceTab') == 'files'):
        result.pop('locationListing', None)
    return result
