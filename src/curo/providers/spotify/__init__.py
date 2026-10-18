"""Spotify provider package."""

from curo.providers.spotify.adapter import SpotifyProvider
from curo.providers.spotify.session import SpotifySessionManager

__all__ = ["SpotifyProvider", "SpotifySessionManager"]

