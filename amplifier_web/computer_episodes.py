"""Trusted bounded sponsors for the independently mounted desktop controller.

This adapter owns task lifetime, not input permission or native release. The
bundle supplies opaque episode identities, admission fences and worker drains.
"""
from __future__ import annotations

import asyncio
from contextvars import ContextVar
from dataclasses import dataclass, field
from weakref import WeakKeyDictionary


_CURRENT = ContextVar("unified_computer_episode", default=None)


@dataclass(eq=False)
class Episode:
    sponsor: object
    key: object
    valid: object
    controller: object
    bounded: bool
    generation: str | None = None
    interrupted: bool = False
    closing: bool = False
    status: dict = field(default_factory=dict)
    begin: object = None
    heartbeat: object = None
    settlement: object = None
    users: int = 0


class ComputerEpisodes:
    """One worker's retained registry, including detached child settlements."""

    def __init__(self, emit=lambda event: None):
        self.sponsors = {}
        self.pending = set()
        self.unresolved = {}
        self.emit = emit
        self.halted = False

    def install(self, coordinator, capture):
        existing = coordinator.get_capability("web.computer_episodes")
        if existing is not None:
            existing.wrap_tools()
            return existing
        sponsor = Sponsor(self, coordinator, capture)
        self.sponsors[coordinator] = sponsor
        coordinator.register_capability("web.computer_episodes", sponsor)
        sponsor.wrap_tools()
        original = coordinator.mount

        async def mount(mount_point, module, name=None):
            if mount_point == "tools":
                module = sponsor.wrap(name or getattr(module, "name", None), module)
            return await original(mount_point, module, name=name)

        coordinator.mount = mount
        hooks = getattr(coordinator, "hooks", None)
        if hooks is not None:
            from amplifier_core import HookResult
            async def entering(event, data):
                name = data.get("tool_name")
                if name in {"computer", "desktop", "ui_use"}:
                    # Capture before approval/Work awaits, never from argument
                    # owner IDs or from whichever generation is current later.
                    control = sponsor.controls.get(asyncio.current_task())
                    captured = control[1] if control and control[0] == data.get("tool_call_id") else capture()
                    mounted = (coordinator.get("tools") or {}).get(name)
                    if captured is not None:
                        key, valid = captured
                        captured = key, lambda: (
                            (coordinator.get("tools") or {}).get(name) is mounted and valid())
                    sponsor.invocations[asyncio.current_task()] = sponsor.capture_invocation(captured)
                return HookResult()
            hooks.register("tool:pre", entering, priority=-1000,
                           name="unified-computer-sponsor")
        return sponsor

    def retain(self, awaitable):
        task = asyncio.create_task(awaitable)
        self.pending.add(task)
        task.add_done_callback(self.pending.discard)
        return task

    def fence(self, coordinator=None, *, halt=False, terminate=False):
        if coordinator is None:
            self.halted |= halt
        for owner, sponsor in tuple(self.sponsors.items()):
            if coordinator is None or owner is coordinator:
                sponsor.stop_epoch += 1
                sponsor.halted |= halt
                sponsor.terminated |= terminate
                sponsor.finish(interrupted=True)

    def resume(self):
        """Clear only the host UI latch; never the bundle's durable halt/token."""
        self.halted = False
        for sponsor in self.sponsors.values():
            sponsor.halted = False
            sponsor.stop_epoch += 1

    async def close(self):
        self.fence(terminate=True)
        # Never cancel these tasks with the model awaiter. Only the controller
        # can prove that its real workers drained and release was confirmed.
        while self.pending:
            await asyncio.shield(asyncio.gather(*tuple(self.pending), return_exceptions=True))
        self.sponsors.clear()


class Sponsor:
    def __init__(self, registry, coordinator, capture):
        self.registry, self.coordinator, self.capture = registry, coordinator, capture
        self.episodes = set()
        self.lazy = {}
        self.ended = []
        self.last_status = {"state": "unavailable", "release_confirmed": False}
        self.unconfirmed = {}
        self.halted = registry.halted
        self.terminated = False
        self.invocations = WeakKeyDictionary()
        self.controls = WeakKeyDictionary()
        self.stop_epoch = 0
        self.lock = asyncio.Lock()

    def publish(self, episode):
        self.last_status = episode.status
        # No prompts, coordinates, screenshots or permission arguments.
        try:
            self.registry.emit({"type": "computer.episode", "sessionId": self.coordinator.session_id,
                                **episode.status})
        except Exception:
            pass  # Observability cannot change the tool's original outcome.

    def wrap(self, name, tool):
        if name not in {"computer", "desktop", "ui_use"} or getattr(tool, "_computer_sponsor", None) is self:
            return tool
        if getattr(tool, "_work_delivery_guard", None) is not None:
            # WorkCheckedTool calls the sponsor inside its authorized body,
            # preserving its issued-wrapper identity and native inspection.
            return tool
        return EpisodeTool(tool, name, self)

    def wrap_tools(self):
        tools = self.coordinator.get("tools") or {}
        for name, tool in tuple(tools.items()):
            tools[name] = self.wrap(name, tool)

    def capture_invocation(self, captured):
        if captured is None:
            return None
        key, valid = captured
        epoch = self.stop_epoch
        return key, lambda: not self.halted and not self.terminated and self.stop_epoch == epoch and valid()

    async def start(self, key, valid, bounded):
        controller = self.coordinator.get_capability("computer_use.episodes")
        if controller is None:
            # Explicit old pins and opt-outs retain their existing behavior.
            return None
        episode = Episode(self, key, valid, controller, bounded)
        self.episodes.add(episode)

        async def begin():
            try:
                episode.status = await controller.begin()
                identity = episode.status.get("identity") or {}
                episode.generation = identity.get("generation")
                self.publish(episode)
                if episode.closing or not valid():
                    self.settle(episode, interrupted=True)
                elif episode.generation:
                    episode.heartbeat = asyncio.create_task(self.heartbeat(episode))
                return episode
            except BaseException:
                cached = controller.cached_status()
                identity = cached.get("identity") or {}
                episode.generation = identity.get("generation")
                episode.status = {"state": "unknown", "release_confirmed": False,
                                  "identity": identity or None, "reason": "episode_begin_unconfirmed"}
                self.publish(episode)
                raise

        # Stop/cancellation can overtake target readiness. Retain the begin so
        # the exact acquired identity is fenced, even after its awaiter leaves.
        episode.begin = self.registry.retain(begin())
        try:
            return await asyncio.shield(episode.begin)
        except BaseException:
            self.settle(episode, interrupted=True)
            raise

    async def heartbeat(self, episode):
        try:
            while not episode.closing:
                await asyncio.sleep(1)
                if not episode.valid():
                    self.settle(episode, interrupted=True)
                    return
                await episode.controller.heartbeat(episode.generation)
        except asyncio.CancelledError:
            raise
        except Exception:
            self.settle(episode, interrupted=True)

    def settle(self, episode, *, interrupted=False):
        episode.interrupted |= interrupted
        episode.closing = True
        if episode.heartbeat:
            episode.heartbeat.cancel()
        if episode.generation:
            try:
                episode.status = episode.controller.fence(
                    episode.generation, interrupted=episode.interrupted)
            except Exception:
                episode.status = {"state": "unknown", "release_confirmed": False,
                                  "reason": "episode_fence_unconfirmed"}
            self.publish(episode)
        if episode.settlement is None:
            episode.settlement = self.registry.retain(self.drain(episode))
        return episode.settlement

    async def drain(self, episode):
        try:
            if episode.begin:
                try:
                    await asyncio.shield(episode.begin)
                except BaseException:
                    if not episode.generation:
                        raise
            if episode.generation:
                # Begin may have completed after the synchronous Stop fence.
                episode.status = episode.controller.fence(
                    episode.generation, interrupted=episode.interrupted)
                self.publish(episode)
                episode.status = await episode.controller.end(
                    episode.generation, interrupted=episode.interrupted)
                self.publish(episode)
                # A bounded controller drain may return pending actual native
                # workers. Keep custody/settlement alive; model cancellation
                # or a timed-out first drain is not a hand-back.
                while isinstance(episode.status.get("workers"), int) and episode.status["workers"] > 0:
                    await asyncio.sleep(.1)
                    try:
                        episode.status = await episode.controller.status(episode.generation)
                    except Exception as exc:
                        # Passive status is safe to reconcile; never replay an
                        # uncertain input or end. Keep the actual controller
                        # and identity alive while worker custody is unknown.
                        episode.status = {**episode.status, "state": "unknown",
                            "release_confirmed": False, "reason": "episode_status_unconfirmed",
                            "errorType": type(exc).__name__}
                        self.publish(episode)
                        await asyncio.sleep(.5)
                        continue
                    self.publish(episode)
                    if episode.status.get("workers") == 0:
                        episode.status = await episode.controller.end(
                            episode.generation, interrupted=episode.interrupted)
                        self.publish(episode)
        except BaseException as exc:
            episode.status = {"state": "unknown", "release_confirmed": False,
                              "reason": "episode_release_unconfirmed", "errorType": type(exc).__name__}
            self.publish(episode)
        finally:
            if episode.generation and (not episode.status.get("release_confirmed")
                    or episode.status.get("indicator_settled") is False):
                self.unconfirmed[episode.generation] = dict(episode.status)
                self.registry.unresolved[(id(self.coordinator), episode.generation)] = episode
            self.episodes.discard(episode)
            if self.lazy.get(episode.key) is episode:
                self.lazy.pop(episode.key, None)

    def finish(self, key=None, *, interrupted=False):
        if key is not None:
            self.ended = (self.ended + [key])[-32:]
        for episode in tuple(self.episodes):
            if key is None or episode.key == key:
                self.settle(episode, interrupted=interrupted)

    def observe(self, event):
        if event.get("type") in {"generation.finished", "generation.failed", "generation.detached"}:
            self.finish(event.get("generation_id"), interrupted=event["type"] != "generation.finished")

    async def close(self, *, interrupted=False):
        self.terminated = True
        self.finish(interrupted=interrupted)
        settlements = [episode.settlement for episode in self.episodes if episode.settlement]
        if settlements:
            await asyncio.shield(asyncio.gather(*settlements, return_exceptions=True))
        self.registry.sponsors.pop(self.coordinator, None)

    async def invoke(self, name, arguments, execute):
        bounded = name == "ui_use" and arguments.get("surface") == "computer"
        if name == "ui_use" and not bounded:
            return await execute(arguments)
        if self.coordinator.get_capability("computer_use.episodes") is None:
            return await execute(arguments)
        if self.halted or self.terminated:
            raise asyncio.CancelledError("Computer use is stopped for this mounted sponsor.")
        epoch = self.stop_epoch
        inherited = _CURRENT.get()
        if inherited is not None and inherited.sponsor is self:
            if inherited.closing or not inherited.valid():
                raise asyncio.CancelledError("The computer task sponsor is no longer current.")
            return await execute(arguments)
        task = asyncio.current_task()
        captured = (self.invocations.pop(task) if task in self.invocations else
                    self.capture_invocation(self.capture()))
        if captured is None:
            raise asyncio.CancelledError("No current computer task execution owner.")
        key, valid = captured
        if key in self.ended or not valid():
            raise asyncio.CancelledError("The computer task execution owner ended.")
        # A direct control without a native generation has only its own awaited
        # invocation lifetime, not an invented persistent model generation.
        bounded = bounded or key is None
        if name == "desktop" and arguments.get("action") == "retarget":
            old = self.lazy.get(key)
            if old:
                await asyncio.shield(self.settle(old))
                if not old.status.get("release_confirmed") or old.status.get("indicator_settled") is False:
                    raise RuntimeError("The previous computer target release is unconfirmed.")
            # Retarget owns no input. Never bind the old target's token to the
            # new mount; the next actual operation acquires a fresh identity.
            if self.halted or self.terminated or self.stop_epoch != epoch or not valid() or key in self.ended:
                raise asyncio.CancelledError("Computer target owner ended while awaiting release.")
            return await execute(arguments)
        async with self.lock:
            if self.halted or self.terminated or self.stop_epoch != epoch or not valid():
                raise asyncio.CancelledError("Computer use stopped while waiting for its sponsor.")
            episode = self.lazy.get(key)
            controller = self.coordinator.get_capability("computer_use.episodes")
            if episode is not None and episode.controller is not controller:
                await asyncio.shield(self.settle(episode, interrupted=True))
                episode = None
            if episode is None:
                episode = await self.start(key, valid, bounded)
                if episode is not None:
                    self.lazy[key] = episode
        if episode is None:
            return await execute(arguments)
        if self.halted or self.terminated or self.stop_epoch != epoch or episode.closing or not episode.valid():
            self.settle(episode, interrupted=True)
            raise asyncio.CancelledError("The computer task was interrupted during readiness.")
        token = _CURRENT.set(episode)
        episode.users += 1
        interrupted = False
        try:
            if episode.generation:
                with episode.controller.bind(episode.generation):
                    return await execute(arguments)
            return await execute(arguments)  # Unsupported target, no feature claim.
        except BaseException:
            interrupted = True
            raise
        finally:
            _CURRENT.reset(token)
            episode.users -= 1
            if (episode.bounded and not episode.users) or interrupted:
                settlement = self.settle(episode, interrupted=interrupted)
                # Shield the actual drain, not the original tool operation.
                # Repeated cancellation leaves the retained settlement running.
                try:
                    await asyncio.shield(settlement)
                except asyncio.CancelledError:
                    self.settle(episode, interrupted=True)
                    if not interrupted:
                        raise


class EpisodeTool:
    def __init__(self, original, name, sponsor):
        object.__setattr__(self, "original", original)
        object.__setattr__(self, "mounted_name", name)
        object.__setattr__(self, "_computer_sponsor", sponsor)

    def __getattr__(self, name):
        return getattr(self.original, name)

    def __setattr__(self, name, value):
        # Keep policy callbacks/native intents on the tool which executes.
        setattr(self.original, name, value)

    async def execute(self, arguments):
        return await self._computer_sponsor.invoke(self.mounted_name, arguments, self.original.execute)