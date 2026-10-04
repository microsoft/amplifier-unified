from .targets import definitions as target_definitions

def definitions(schema, string):
    task = {'sessionId': string(200)}
    site = {**task, 'siteId': {'type': 'string', 'pattern': '^[a-z0-9][a-z0-9-]{0,62}$'}}
    request = {'requestId': {**string(200), 'pattern': '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$'}}
    release = {**task, 'releaseId': {**string(100), 'minLength': 1}}
    revision = {'expectedRevision': {'type': 'integer', 'minimum': 0}}
    actions = {
        'publishing.list': ('Inspect this task’s releases, deployments and durable receipts on the explicit or selected target. Remote reads use the inspected private service identity; no work is started or replayed.', schema(task)),
        'publishing.build': ('Capture an existing built static output directory inside this task’s local execution folder as an immutable release, importing its exact static bytes when the selected target is remote. Run the project’s own build first. No build command is executed, no model starts, and no site is deployed. Reuse requestId only for an exact retry.', schema({**site, **request, 'sourcePath': {**string(4000), 'minLength': 1}})),
        'publishing.preview': ('Start a separate loopback preview of an exact saved release. It has no access to the app origin. Preview is not review or deployment. Reuse requestId for exact retry and inspect current status separately.', schema({**release, **request})),
        'publishing.review': ('Record a review note on the exact immutable release after inspecting its content. This does not prove visual inspection or deploy it.', schema({**release, **request, 'note': {**string(4000), 'minLength': 1}})),
        'publishing.deploy': ('Explicitly activate a reviewed release on the selected, inspected target using its current revision. The target reports its actual bind and access policy; this action configures no authentication, TLS or public route. Historical receipts do not prove current liveness.', schema({**site, **release, **request, **revision})),
        'publishing.rollback': ('Explicitly restore a previously deployed, reviewed immutable release using the current site revision. Does not rerun a build or replay unknown effects.', schema({**site, **release, **request, **revision})),
        'publishing.status': ('Read current target deployment status, release identity, access policy and URL. Does not start a listener.', schema(site)),
        'publishing.logs': ('Read durable lifecycle receipts for this task and site. HTTP bodies, credentials and build environment values are not captured.', schema(site)),
        'publishing.stop': ('Stop this site’s target listeners, retaining immutable releases and durable receipts. Requires current revision.', schema({**site, **request, **revision})),
        'publishing.remove': ('Remove this target deployment and previews, retaining releases and receipts for audit. Source files are preserved. Requires current revision.', schema({**site, **request, **revision})),
    }
    for name, (description, spec) in list(actions.items()):
        spec['properties']['targetId'] = {**string(100), 'minLength': 1}
        if name not in {'publishing.list', 'publishing.status', 'publishing.logs'}:
            spec['properties']['targetRevision'] = {'type': 'integer', 'minimum': 0}
            spec['properties']['serviceId'] = {**string(200), 'minLength': 1}
            actions[name] = (description + ' New remote requests must include the targetRevision and serviceId returned by target inspection; exact retries keep their original arguments.', spec)
    actions.update(target_definitions(schema, string))
    return actions

