"""Real SDK consumer fixture; synthetic conditional retirement and delayed hints."""
import asyncio
import sys

from acp import run_agent, RequestError
from external_acp_agent import Fixture
from shared_acp_agent import SharedFixture


class Retirement:
    retired = False

    def on_connect(self, connection):
        super().on_connect(connection)
        asyncio.create_task(self.hints())

    async def hints(self):
        while True:
            await asyncio.sleep(.01)  # Test-owned external event control only.
            marker = self.home / 'notify'
            if marker.exists():
                marker.unlink()
                await self.client.ext_notification('amplifier/retirementRequested',
                    {'sessionId': self.identity_for_retirement})

    async def initialize(self, **kwargs):
        result = await super().initialize(**kwargs)
        if (self.home / 'no-resume').exists():
            result.agent_capabilities.session_capabilities.resume = None
        result.field_meta = {**(result.field_meta or {}), 'com.microsoft.amplifier.retirement': {
            'version': 1, 'method': '_amplifier/retire', 'notification':
                '_amplifier/retirementAvailable' if (self.home / 'legacy-notification').exists()
                else '_amplifier/retirementRequested'}}
        if (self.home / 'awareness').exists():
            result.field_meta.update({'steering': {'supported': True}, 'io.github.bkrabach.agent-instance': {
                'inputContinuity': {'awareness': {'version': 1, 'activeOnly': True, 'promptIdRequired': True}}}})
        return result

    async def new_session(self, **kwargs):
        result = await super().new_session(**kwargs)
        self.identity_for_retirement = result.session_id
        return result

    async def resume_session(self, session_id, **kwargs):
        self.identity_for_retirement = session_id
        return await super().resume_session(session_id=session_id, **kwargs)

    async def prompt(self, **kwargs):
        assert not self.retired, 'A confirmed retired attachment cannot accept another input.'
        if (self.home / 'hold-output').exists() and not kwargs['prompt'][-1].text.startswith('Decide quietly'):
            while not (self.home / 'release-output').exists():
                await asyncio.sleep(.01)
        return await super().prompt(**kwargs)

    async def ext_method(self, method, params):
        if method == 'session/steering':
            identity = params['_meta']['steering']['inputId']
            self.record('observing', inputId=identity)
            while not (self.home / 'release-awareness').exists():
                await asyncio.sleep(.01)
            return {'status': 'injected', '_meta': {'steering': {'inputId': identity}}}
        if method != 'amplifier/retire':
            raise RequestError(-32601, 'Unsupported fixture extension')
        assert params == {'sessionId': self.identity_for_retirement}
        self.record('retire', sessionId=self.identity_for_retirement)
        while (self.home / 'hold-retire').exists() and not (self.home / 'release-retire').exists():
            await asyncio.sleep(.01)
        setting = self.home / 'retirement'
        result = setting.read_text() if setting.exists() else 'retired'
        if result == 'malformed':
            return {'sessionId': 'not-the-attached-session', 'disposition': 'retired'}
        if result == 'silent':
            await asyncio.Event().wait()
        self.retired = result == 'retired'
        return {'sessionId': self.identity_for_retirement, 'disposition': result}


class Single(Retirement, Fixture):
    pass


class Shared(Retirement, SharedFixture):
    pass


if __name__ == '__main__':
    peer = Shared(sys.argv[1], sys.argv[2]) if len(sys.argv) > 2 else Single(sys.argv[1], True)
    asyncio.run(run_agent(peer, use_unstable_protocol=True))
