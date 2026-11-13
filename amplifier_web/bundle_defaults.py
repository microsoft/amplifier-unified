"""Release-owned fallback policy, distinct from explicit bundle.active choices."""
from __future__ import annotations

import hashlib

GENERAL_BUNDLE = 'work'
STARTER_BUNDLES = {'amplifier-development': 'work-amp-dev'}
# Instruction identities from the shipped built-in snapshots, not user files.
# 12056a077 / 4108e309b / 7f57086df / 4ed701e43 / b55e33ea9.
_LEGACY_INSTRUCTIONS = {
    5: 'b37ad81e613a3ab0c5230da292c7a2329cd19dc9e520c4478784840b25141132',
    6: 'b37ad81e613a3ab0c5230da292c7a2329cd19dc9e520c4478784840b25141132',
    7: '4d00656c459a889e062563862f1f857b4603d4281c0e5a57e49bc276b40f92e4',
    8: 'aee704c0c1ccec6e9484dafed5b5764de39b050033c54051223eb064ec6be4ab',
    9: 'aee704c0c1ccec6e9484dafed5b5764de39b050033c54051223eb064ec6be4ab',
}


def shipped_bundle(starter=None):
    return STARTER_BUNDLES.get(starter, GENERAL_BUNDLE) if isinstance(starter, str) else GENERAL_BUNDLE


def automatic_starter(starter):
    """Recognize host snapshots, never a custom starter or a familiar string alone."""
    if (not isinstance(starter, dict) or starter.get('builtIn') is not True
            or starter.get('id') != 'amplifier-development'):
        return False
    if starter.get('bundleDefault') == 'release':
        return True
    revision = starter.get('revision')
    # Retained built-in versions before release-default provenance was explicit.
    # Unknown/newer snapshots and custom source bindings cannot borrow this rule.
    return (type(revision) is int and revision in _LEGACY_INSTRUCTIONS
            and set(starter) == {'id', 'builtIn', 'revision', 'name', 'description',
                                 'instructions', 'bundle', 'repositories',
                                 'trackResources', 'scratch', 'rootGit'}
            and not starter.get('bundleSource')
            and isinstance(starter.get('instructions'), str)
            and isinstance(starter.get('bundle'), str)
            and hashlib.sha256(starter.get('instructions', '').encode()).hexdigest() == _LEGACY_INSTRUCTIONS[revision]
            and starter.get('name') == 'Amplifier development'
            and starter.get('description') == 'Amplifier, Core and Foundation as child repositories, with working memory and local workspace Git.'
            and all(starter.get(key) is True for key in ('trackResources', 'scratch', 'rootGit'))
            and starter.get('repositories') == [
                {'url': f'https://github.com/microsoft/{name}.git', 'directory': name, 'ref': ''}
                for name in ('amplifier', 'amplifier-core', 'amplifier-foundation')]
            and starter.get('bundle') in (
                {'', 'work-amp-dev'} if revision == 9 else {'', 'anchors-amp-dev'}))


def fallback(settings):
    return shipped_bundle(settings.get('bundle', {}).get('releaseDefault'))