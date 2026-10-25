"""YouTube Music provider adapter stub."""

from curo.core.registry import ProviderRegistry
from curo.providers.base import ProviderStub


class YouTubeMusicProvider(ProviderStub):
    """YouTube Music streaming service adapter."""

    _name = "youtube_music"
    _display_name = "YouTube Music"


ProviderRegistry.register("youtube_music", YouTubeMusicProvider)
ProviderRegistry.register("ytmusic", YouTubeMusicProvider)

