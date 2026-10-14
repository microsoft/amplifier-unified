"""This regression also runs unchanged against the preceding synchronous host."""
from amplifier_web.shell_resources import scope_for
from test_shell_modules import service


async def test_cold_bundled_query_never_calls_synchronous_directory_reader(service, monkeypatch):
    service._save_changes(sessions={'alpha', 'beta'}, globals={'workspaces'})
    service.clients.attach('cold-browser')
    def forbidden(*args, **kwargs):
        raise AssertionError('Cold catalog query owns the main event loop')
    monkeypatch.setattr(service.directory, 'chats', forbidden)
    with service.clients.bind('cold-browser'):
        owner = service.shell.resources
        owner.register('cold-browser', 1, ['recentNavigation'], scope_for(service.state))
        frame = owner.snapshot('cold-browser')
        assert frame['instances']['chats']['pending']['recentNavigation']['status'] == 'pending'
