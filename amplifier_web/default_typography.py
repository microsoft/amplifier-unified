"""Upgrade only exact, unmodified historical default skins."""
import hashlib

# Previous stock CSS identities. Never match by name alone:
# a user can edit a skin while retaining its original display name.
PREVIOUS_DEFAULTS = frozenset(['0c555ff9fc354a6eb62139922f28aa5ed1894cbd5454a5b33723848961fce8d9', 'd0544c7ae97bcd4a952bcbfe190cefe2da6ce35f65a513842cb296d7775d06c8', 'ffc5525ef3205bee010c48c5408c0340c96c90bdd899f8adb22bfc628dca488b', 'f4744740c60ba5b48c14514901fb20a466454bae714a98581283abbc3d153cd3', '50e1191b90887d6035e8a17cc89234341662c6f28c8b45bbe875b6ed01310ad7', 'eb24ed026151a882f8a29474b1bb26287f4b30129069ff17cb39ce501168747a', '8b24f78405719a6e1b7350749ac8c510855cc6ff106b81a2d6e120ef14f55652', '9bc0b6126217a4218f7f5e25ffe8bf2b4ebc43607433d4b48147b1c848fbcf41'])


# Exact stock presets shipped in 0.20.93; customized CSS never matches.
PREVIOUS_PRESETS = {'5e82b64f878c70520cbd7c8ff14441ee9e107cbf1a2992c4b6b971584abf86de': 'aurora', '95d4802ab090033bd01c88a5cfebb043fdfca52e01e208a6d0f67237e5af17a0': 'atelier', 'a79269f281171cc15fccf0d48d567eab7f1b6fbc466a9a320a8f340b825dda43': 'graphite'}

# Exact unmodified stock presets from 0.20.112. Preserve edited user skins.
PREVIOUS_PRESETS.update({
    '2e3b54e336f8339cc7b08e0dc8cc920dafe1204b2093a4c82bc0ef279f42349f': 'aurora',
    '45d4e52b2efd47cb3c870cde6fb6d7aa800d8a6277ae2c7f5d7c7b309122126f': 'atelier',
    '6932a122e56e1728ef10b39c4278bc3ff622b881c6afc0ab954aeb4a64efd93d': 'graphite',
})

# Exact stock presets from 0.20.114–0.20.116, before panel surface tokens.
PREVIOUS_PRESETS.update({
    'fca1e95f71447a1c1479e531f33c6e429c58aa3ee9f01e03d2092865c9c29744': 'aurora',
    '19eacc61e596337241e3af4839f4fd29d47ad6133f25e3d1c264f3ba6561c85c': 'atelier',
    'bca0a15d0c3407d79bc835df5f6817e10716e3cfbfbbcdee108cfb9e8d60a9f0': 'graphite',
})

def upgrade_default(state, css):
    theme = state.get('theme', {})
    if theme.get('name') in {'Amplifier Unified', 'Amplifier'} and hashlib.sha256(theme.get('css', '').encode()).hexdigest() in PREVIOUS_DEFAULTS:
        state['theme'] = {**theme, 'css': css}
    elif (identity := PREVIOUS_PRESETS.get(hashlib.sha256(theme.get('css', '').encode()).hexdigest())):
        from types import SimpleNamespace
        from .theme_library import preset
        updated = preset(SimpleNamespace(default_theme=lambda: css), identity)
        state['theme'] = {**theme, 'css': updated['css']}
