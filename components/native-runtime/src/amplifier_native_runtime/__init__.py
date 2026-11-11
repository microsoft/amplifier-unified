"""Shared native application lifecycle policy; no runtime starts on import."""

from .release import WorkerReleaseParticipant

__all__ = ["WorkerReleaseParticipant"]
