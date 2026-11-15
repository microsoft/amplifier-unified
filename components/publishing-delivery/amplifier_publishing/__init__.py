"""Portable, durable, loopback-only static release publishing."""

from .publisher import Publisher, PublishingError

__all__ = ["Publisher", "PublishingError"]
