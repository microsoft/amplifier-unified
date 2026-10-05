"""Owned offline provider for public setup/model discovery; never inference/auth."""
class OwnedFixtureProvider:
    def __init__(self, config=None): self.config = config or {}
    def get_info(self): return {'id':'owned-fixture','display_name':'Owned fixture','capabilities':['reasoning'],'config_fields':[]}
    def get_config_schema(self): return {'fields':[]}
    async def list_models(self): return [{'id':'fixture-one','display_name':'Fixture one','defaults':{'reasoning_effort':'medium'}},{'id':'fixture-two','display_name':'Fixture two'}]
    async def complete(self, *args, **kwargs): raise AssertionError('No inference permitted')
