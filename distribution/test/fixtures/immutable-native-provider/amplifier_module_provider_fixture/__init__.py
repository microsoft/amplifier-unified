"""Offline mount-only fixture: a completion is an acceptance-test failure."""
from pathlib import Path

from amplifier_core.models import ProviderInfo


class FixtureProvider:
    name = "fixture"

    def __init__(self, config):
        self.config = config or {}

    def get_info(self):
        return ProviderInfo(id="fixture", display_name="Offline fixture",
                            defaults={"model": "fixture-model", "max_tokens": 4096},
                            capabilities=["tools", "completion:single_attempt:v2"])

    def parse_tool_calls(self, response):
        return response.tool_calls or []

    async def list_models(self):
        return [{"id": "fixture-model"}]

    async def complete(self, request, **kwargs):
        Path(self.config["completionAudit"]).write_text("unexpected completion")
        raise AssertionError("Mount-only fixture must not complete a prompt")

    async def close(self):
        pass


async def mount(coordinator, config=None):
    await coordinator.mount("providers", FixtureProvider(config), name="fixture")
