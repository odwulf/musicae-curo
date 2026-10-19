"""Deezer provider package."""

from curo.providers.deezer.adapter import DeezerProvider
from curo.providers.deezer.session import DeezerSessionManager

__all__ = ["DeezerProvider", "DeezerSessionManager"]
