"""Standardized MusicProvider interface (SPI) for Musicae Curo.

Every streaming service adapter (Tidal, Deezer, Spotify, Qobuz, etc.) implements this
interface to provide unified playlist, mix, and catalog operations.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from curo.core.models import Album, CuratedMix, Playlist, Track


class MusicProvider(ABC):
    """Abstract interface for music streaming providers."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider name/identifier (e.g. 'tidal', 'deezer', 'spotify')."""
        ...

    @property
    @abstractmethod
    def display_name(self) -> str:
        """User-friendly display name (e.g. 'Tidal', 'Deezer', 'Spotify')."""
        ...

    # --- Authentication & Session ---
    @abstractmethod
    def login(self) -> bool:
        """Initiate authentication flow (e.g. device OAuth or credentials)."""
        ...

    @abstractmethod
    def logout(self) -> None:
        """Clear cached session tokens and credentials."""
        ...

    @abstractmethod
    def is_authenticated(self) -> bool:
        """Return True if an active, valid session is established."""
        ...

    @abstractmethod
    def get_session_info(self) -> Dict[str, Any]:
        """Return user profile and token status metadata."""
        ...

    # --- Favorites / Liked Songs ---
    @abstractmethod
    def get_favorite_tracks(self) -> List[Track]:
        """Fetch all favorite/liked tracks for the authenticated user with full pagination."""
        ...

    # --- Playlists ---
    @abstractmethod
    def get_user_playlists(self) -> List[Playlist]:
        """Fetch all playlists owned by the authenticated user."""
        ...

    @abstractmethod
    def get_playlist(self, identifier: str) -> Optional[Playlist]:
        """Fetch a single playlist by ID or exact title match."""
        ...

    @abstractmethod
    def get_playlist_tracks(self, playlist_id: str) -> List[Track]:
        """Fetch all tracks in a playlist in order."""
        ...

    @abstractmethod
    def create_playlist(
        self, title: str, description: str = "", is_public: bool = False, folder: Optional[str] = None
    ) -> Playlist:
        """Create a new playlist and return the created domain Playlist object.

        If folder is specified and supported by the provider, the playlist is created in
        or moved to that folder.
        """
        ...

    def get_folders(self) -> List[Dict[str, Any]]:
        """Fetch all playlist folders owned by the authenticated user."""
        return []

    def create_folder(self, name: str) -> Optional[str]:
        """Create a new playlist folder and return its ID, or None if unsupported."""
        return None

    def move_playlist_to_folder(self, playlist_id: str, folder: str) -> bool:
        """Move a playlist into a folder specified by name or folder ID."""
        return False

    @abstractmethod
    def add_tracks_to_playlist(
        self, playlist_id: str, track_ids: List[str], position: Optional[int] = None
    ) -> bool:
        """Add track IDs to a playlist, optionally at a specific 0-based index."""
        ...

    @abstractmethod
    def remove_track_from_playlist_by_index(self, playlist_id: str, index: int) -> bool:
        """Remove a track at a specific 0-based index from the playlist."""
        ...

    @abstractmethod
    def clear_playlist(self, playlist_id: str) -> bool:
        """Remove all tracks from a playlist."""
        ...

    # --- Discovery & Curated Mixes ---
    @abstractmethod
    def get_curated_mixes(self) -> List[CuratedMix]:
        """Fetch personalized algorithmic or editorial discovery mixes."""
        ...

    @abstractmethod
    def get_mix_tracks(self, mix_id: str) -> List[Track]:
        """Fetch all tracks belonging to a curated mix."""
        ...

    # --- Catalog Lookup & Search ---
    @abstractmethod
    def get_track(self, track_id: str) -> Optional[Track]:
        """Fetch track metadata by provider ID."""
        ...

    @abstractmethod
    def get_tracks_by_isrc(self, isrc: str) -> List[Track]:
        """Find tracks matching an ISRC sound recording code."""
        ...

    @abstractmethod
    def get_album(self, album_id: str) -> Optional[Album]:
        """Fetch album metadata by provider ID."""
        ...

    @abstractmethod
    def get_albums_by_barcode(self, upc: str) -> List[Album]:
        """Find albums matching a UPC / EAN barcode."""
        ...

    @abstractmethod
    def get_album_tracks(self, album_id: str) -> List[Track]:
        """Fetch all tracks belonging to an album."""
        ...

    @abstractmethod
    def search_tracks(self, query: str, limit: int = 10) -> List[Track]:
        """Search track catalog by query string."""
        ...

