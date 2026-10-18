"""Service for playlist inspection, creation, track management, and exports."""

from __future__ import annotations

import csv
import io
import json
import logging
from typing import Any, Dict, List, Optional, Union

import tidalapi
from tidalapi.playlist import UserPlaylist

logger = logging.getLogger(__name__)


class PlaylistService:
    """Provides high-level playlist operations on top of an active tidalapi.Session."""

    def __init__(self, session: tidalapi.Session) -> None:
        self.session = session
        self.user = session.user

    def list_playlists(self) -> List[Dict[str, Any]]:
        """Return list of user playlists with metadata."""
        playlists = self.user.playlists()
        result = []
        for pl in playlists:
            result.append({
                "id": pl.id,
                "name": pl.name,
                "description": getattr(pl, "description", "") or "",
                "num_tracks": getattr(pl, "num_tracks", 0),
                "duration_seconds": getattr(pl, "duration", 0),
                "public": getattr(pl, "public", False),
                "last_updated": getattr(pl, "last_updated", None),
            })
        return result

    def get_playlist(self, identifier: str) -> Optional[UserPlaylist]:
        """Find a playlist by its ID or exact name match."""
        # 1. Try direct ID lookup
        try:
            pl = self.session.playlist(identifier)
            if pl:
                return pl
        except Exception:
            pass

        # 2. Try match against user's playlist names
        identifier_lower = identifier.strip().lower()
        for pl in self.user.playlists():
            if pl.id == identifier or pl.name.lower() == identifier_lower:
                return pl

        return None

    def get_tracks(self, identifier: str) -> List[Any]:
        """Fetch all tracks for the specified playlist ID or name."""
        pl = self.get_playlist(identifier)
        if not pl:
            raise ValueError(f"Playlist not found: {identifier}")
        return pl.tracks()

    def create_playlist(
        self,
        title: str,
        description: str = "",
        public: bool = False,
    ) -> UserPlaylist:
        """Create a new playlist."""
        pl = self.user.create_playlist(title=title, description=description)
        if public:
            try:
                pl.set_playlist_public()
            except Exception as e:
                logger.warning(f"Failed to set playlist {title} to public: {e}")
        return pl

    def get_or_create_playlist(
        self,
        title: str,
        description: str = "",
        public: bool = False,
    ) -> UserPlaylist:
        """Find an existing user playlist with matching name or create a new one."""
        existing = self.get_playlist(title)
        if existing and isinstance(existing, UserPlaylist):
            return existing
        return self.create_playlist(title=title, description=description, public=public)

    def add_tracks(
        self,
        playlist: UserPlaylist,
        track_ids: List[Union[str, int]],
        allow_duplicates: bool = False,
        chunk_size: int = 50,
    ) -> int:
        """Add tracks to a playlist in safe batches to avoid API limits.

        Returns total number of items added.
        """
        if not track_ids:
            return 0

        str_ids = [str(t) for t in track_ids]
        total_added = 0

        for i in range(0, len(str_ids), chunk_size):
            chunk = str_ids[i : i + chunk_size]
            try:
                added = playlist.add(chunk, allow_duplicates=allow_duplicates)
                total_added += len(added) if added else len(chunk)
            except Exception as e:
                logger.error(f"Error adding chunk {i//chunk_size + 1} to playlist '{playlist.name}': {e}")
                raise

        return total_added

    def clear_playlist(self, playlist: UserPlaylist, chunk_size: int = 50) -> bool:
        """Clear all tracks from a playlist."""
        try:
            return playlist.clear(chunk_size=chunk_size)
        except Exception as e:
            logger.error(f"Failed to clear playlist '{playlist.name}': {e}")
            raise

    def export_playlist(self, identifier: str, export_format: str = "json") -> str:
        """Export playlist tracks in json, m3u8, or csv format."""
        pl = self.get_playlist(identifier)
        if not pl:
            raise ValueError(f"Playlist not found: {identifier}")

        tracks = pl.tracks()
        fmt = export_format.lower().strip()

        if fmt == "json":
            data = {
                "id": pl.id,
                "name": pl.name,
                "description": getattr(pl, "description", ""),
                "track_count": len(tracks),
                "tracks": [
                    {
                        "id": t.id,
                        "title": t.name,
                        "artist": getattr(getattr(t, "artist", None), "name", "Unknown Artist"),
                        "album": getattr(getattr(t, "album", None), "name", "Unknown Album"),
                        "duration_seconds": getattr(t, "duration", 0),
                        "isrc": getattr(t, "isrc", ""),
                    }
                    for t in tracks
                ],
            }
            return json.dumps(data, indent=2, ensure_ascii=False)

        elif fmt in ("m3u", "m3u8"):
            lines = ["#EXTM3U", f"#PLAYLIST:{pl.name}"]
            for t in tracks:
                artist = getattr(getattr(t, "artist", None), "name", "Unknown Artist")
                duration = getattr(t, "duration", -1)
                lines.append(f"#EXTINF:{duration},{artist} - {t.name}")
                lines.append(f"https://tidal.com/browse/track/{t.id}")
            return "\n".join(lines)

        elif fmt == "csv":
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(["id", "title", "artist", "album", "duration_seconds", "isrc"])
            for t in tracks:
                artist = getattr(getattr(t, "artist", None), "name", "Unknown Artist")
                album = getattr(getattr(t, "album", None), "name", "Unknown Album")
                writer.writerow([
                    t.id,
                    t.name,
                    artist,
                    album,
                    getattr(t, "duration", 0),
                    getattr(t, "isrc", ""),
                ])
            return output.getvalue()

        else:
            raise ValueError(f"Unsupported export format: {export_format}. Use json, m3u8, or csv.")

