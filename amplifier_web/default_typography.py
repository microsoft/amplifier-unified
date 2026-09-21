"""Upgrade only exact, unmodified historical default skins."""
import hashlib

# Published default CSS identities through 0.19.15. Never match by name alone:
# a user can edit a skin while retaining its original display name.
PREVIOUS_DEFAULTS = frozenset(['ffc5525ef3205bee010c48c5408c0340c96c90bdd899f8adb22bfc628dca488b', 'f4744740c60ba5b48c14514901fb20a466454bae714a98581283abbc3d153cd3', '50e1191b90887d6035e8a17cc89234341662c6f28c8b45bbe875b6ed01310ad7', 'eb24ed026151a882f8a29474b1bb26287f4b30129069ff17cb39ce501168747a', '8b24f78405719a6e1b7350749ac8c510855cc6ff106b81a2d6e120ef14f55652', '9bc0b6126217a4218f7f5e25ffe8bf2b4ebc43607433d4b48147b1c848fbcf41'])


def upgrade_default(state, css):
    theme = state.get('theme', {})
    if theme.get('name') == 'Amplifier Unified' and hashlib.sha256(theme.get('css', '').encode()).hexdigest() in PREVIOUS_DEFAULTS:
        state['theme'] = {**theme, 'css': css}
