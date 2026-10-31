"""Shared native tool dispatch without test-module collection dependencies."""


async def execute_tool(coordinator, name, arguments):
    """Use the actual loop's hooks, cancellation and per-task dispatch binding."""
    from amplifier_core.message_models import ToolCall
    from amplifier_module_loop_streaming import StreamingOrchestrator
    from uuid import uuid4
    tools = coordinator.get('tools')
    observed = []
    class Capture:
        def __getattr__(self, attribute):
            return getattr(tools[name], attribute)
        async def execute(self, value):
            result = await tools[name].execute(value)
            observed.append(result)
            return result
    await StreamingOrchestrator({})._execute_tool_only(
        ToolCall(id=str(uuid4()), name=name, arguments=arguments),
        {**tools, name: Capture()}, coordinator.hooks, None, coordinator)
    assert len(observed) == 1
    return observed[0]
