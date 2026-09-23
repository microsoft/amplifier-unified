"""Voice choices shared by the UI, agent settings action and call transport."""
import json
from pathlib import Path
CATALOG=json.loads(Path(__file__).with_suffix('.json').read_text())
def voices_for(model):
    return {voice['id'] for row in CATALOG['models'] if row['id']==model for voice in row['voices']}
def selected_voice(model,name='marin'):
    return name if name in voices_for(model) else 'marin'
