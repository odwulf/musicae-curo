"""Spotify provider adapter stub."""

from curo.core.registry import ProviderRegistry
from curo.providers.base import ProviderStub


class SpotifyProvider(ProviderStub):
    """Spotify streaming service adapter."""

    _name = "spotify"
    _display_name = "Spotify"


ProviderRegistry.register("spotify", SpotifyProvider)

