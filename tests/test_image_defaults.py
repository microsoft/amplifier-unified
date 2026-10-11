import copy
import pytest
from amplifier_web.image_defaults import apply_defaults, preference

class ImageProvider:
    closed = []
    seen = []
    def __init__(self, config): self.config = config
    def get_image_generation_info(self):
        return {'schemaVersion': 1, 'configKey': 'image_generation', 'automaticModel': 'auto'}
    async def list_image_models(self):
        self.seen.append(self.config['account'])
        if self.config.get('broken'): raise RuntimeError('private account detail')
        return self.config.get('models', [{'id':'image-current'}])
    async def close(self): self.closed.append(self.config['account'])
    async def generate(self, *args): pytest.fail('Selection must never generate or retry an image')

class ChatProvider: pass

def row(name, priority, **config):
    return {'module':'provider-fixture', 'id':name, 'config':{'account':name, 'priority':priority, **config}}

@pytest.mark.asyncio
async def test_default_uses_priority_skips_unsupported_empty_failed_disabled_and_closes():
    ImageProvider.seen.clear(); ImageProvider.closed.clear()
    plan={'providers':[row('later',9),row('failed',2,broken=True),row('empty',3,models=[]),
                       {'module':'provider-chat','config':{'priority':1}},row('chosen',4),
                       {**row('disabled',0),'enabled':False}]}
    before=copy.deepcopy(plan)
    assert await apply_defaults(plan,{},class_loader=lambda name: ChatProvider if name=='provider-chat' else ImageProvider)=='chosen'
    assert ImageProvider.seen==['failed','empty','chosen']
    assert ImageProvider.closed==ImageProvider.seen
    assert plan['providers'][4]['config']['image_generation']=={'enabled':True,'id':'images','model':'auto'}
    for i in (0,1,2,3,5): assert plan['providers'][i]==before['providers'][i]

@pytest.mark.asyncio
async def test_legacy_pin_and_off_are_preserved_without_discovery():
    def forbidden(*args): pytest.fail('Explicit choice must not discover or replace accounts')
    pinned={'providers':[row('kept',1,image_generation={'enabled':True,'model':'pinned','id':'custom'})]}
    before=copy.deepcopy(pinned)
    await apply_defaults(pinned,{},class_loader=forbidden)
    assert pinned==before
    disabled={'providers':[row('off',1,image_generation={'enabled':False}),row('other',2)]}
    await apply_defaults(disabled,{},class_loader=forbidden)
    assert preference({},disabled['providers'])=='off'

@pytest.mark.asyncio
async def test_automatic_override_replaces_legacy_pin_without_changing_chat_model():
    plan={'providers':[row('first',1,default_model='chat-choice',image_generation={'enabled':False}),
                       row('previous',2,image_generation={'enabled':True,'model':'old'})]}
    assert await apply_defaults(plan,{'image_generation':{'mode':'automatic'}},class_loader=lambda _:ImageProvider)=='first'
    assert plan['providers'][0]['config']['default_model']=='chat-choice'
    assert plan['providers'][1]['config']['image_generation']['enabled'] is False
    await apply_defaults(plan,{'image_generation':{'mode':'off'}},class_loader=lambda _:pytest.fail('off'))
    assert not any(row['config']['image_generation']['enabled'] for row in plan['providers'])
