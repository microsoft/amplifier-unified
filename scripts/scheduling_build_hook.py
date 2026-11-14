"""Offline integrity check for Unified's selected component artifacts."""
import importlib.util
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

SPEC = importlib.util.spec_from_file_location("scheduling_delivery", Path(__file__).with_name("scheduling_delivery.py"))
delivery = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(delivery)


class SchedulingDeliveryHook(BuildHookInterface):
    def initialize(self, version, build_data):
        for component in delivery.COMPONENTS:
            delivery.verify(Path(self.root), component=component)

    def finalize(self, version, build_data, artifact_path):
        if self.target_name == "wheel":
            for component in delivery.COMPONENTS:
                delivery.verify_wheel(Path(self.root), artifact_path, editable=version == "editable", component=component)
