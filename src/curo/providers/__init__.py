"""Streaming provider adapters for Musicae Curo."""

import curo.providers.tidal
import curo.providers.deezer
import curo.providers.spotify
import curo.providers.qobuz
import curo.providers.ytmusic
from curo.providers.deezer import DeezerProvider
from curo.providers.spotify import SpotifyProvider
from curo.providers.tidal import TidalProvider

__all__ = ["TidalProvider", "SpotifyProvider", "DeezerProvider"]
