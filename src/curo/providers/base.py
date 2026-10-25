"""Base stub implementation for prospective music providers."""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from curo.core.models import Album, CuratedMix, Playlist, Track
from curo.core.provider import MusicProvider


class ProviderStub(MusicProvider):
    """Base class for streaming providers that are scaffolded for future integration."""

    _name: str = "stub"
    _display_name: str = "Stub"

    def __init__(self, cfg: Optional[Any] = None, **kwargs: Any) -> None:
        self.config = cfg
        self._kwargs = kwargs

    @property
    def name(self) -> str:
        return self._name

    @property
    def display_name(self) -> str:
        return self._display_name

    def _not_implemented(self, feature: str = "This provider") -> None:
        raise NotImplementedError(
            f"{self.display_name} integration is planned and scaffolded, but not yet implemented. "
            f"Contributions welcome! In the meantime, use an active provider such as Tidal."
        )

    def login(self) -> bool:
        self._not_implemented("login")
        return False

    def logout(self) -> None:
        self._not_implemented("logout")

    def is_authenticated(self) -> bool:
        return False

    def get_session_info(self) -> Dict[str, Any]:
        return {"logged_in": False, "provider": self.name, "status": "scaffolded_stub"}

    def get_favorite_tracks(self) -> List[Track]:
        self._not_implemented("get_favorite_tracks")
        return []

    def get_user_playlists(self) -> List[Playlist]:
        self._not_implemented("get_user_playlists")
        return []

    def get_playlist(self, identifier: str) -> Optional[Playlist]:
        self._not_implemented("get_playlist")
        return None

    def get_playlist_tracks(self, playlist_id: str) -> List[Track]:
        self._not_implemented("get_playlist_tracks")
        return []

    def create_playlist(
        self, title: str, description: str = "", is_public: bool = False, folder: Optional[str] = None
    ) -> Playlist:
        self._not_implemented("create_playlist")
        raise NotImplementedError

    def add_tracks_to_playlist(
        self, playlist_id: str, track_ids: List[str], position: Optional[int] = None
    ) -> bool:
        self._not_implemented("add_tracks_to_playlist")
        return False

    def remove_track_from_playlist_by_index(self, playlist_id: str, index: int) -> bool:
        self._not_implemented("remove_track_from_playlist_by_index")
        return False

    def clear_playlist(self, playlist_id: str) -> bool:
        self._not_implemented("clear_playlist")
        return False

    def get_curated_mixes(self) -> List[CuratedMix]:
        self._not_implemented("get_curated_mixes")
        return []

    def get_mix_tracks(self, mix_id: str) -> List[Track]:
        self._not_implemented("get_mix_tracks")
        return []

    def get_track(self, track_id: str) -> Optional[Track]:
        self._not_implemented("get_track")
        return None

    def get_tracks_by_isrc(self, isrc: str) -> List[Track]:
        self._not_implemented("get_tracks_by_isrc")
        return []

    def get_album(self, album_id: str) -> Optional[Album]:
        self._not_implemented("get_album")
        return None

    def get_albums_by_barcode(self, upc: str) -> List[Album]:
        self._not_implemented("get_albums_by_barcode")
        return []

    def get_album_tracks(self, album_id: str) -> List[Track]:
        self._not_implemented("get_album_tracks")
        return []

    def search_tracks(self, query: str, limit: int = 10) -> List[Track]:
        self._not_implemented("search_tracks")
        return []

