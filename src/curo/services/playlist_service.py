"""Service for playlist inspection, creation, track management, and exports."""

from __future__ import annotations

import csv
import datetime
import io
import json
import logging
from typing import Any, Dict, List, Optional, Union

from curo.core.models import Playlist, Track
from curo.core.provider import MusicProvider
from curo.services.metadata_serializer import serialize_playlist, serialize_track

logger = logging.getLogger(__name__)


class PlaylistService:
    """Provides high-level playlist operations on top of an active MusicProvider."""

    def __init__(self, provider: MusicProvider) -> None:
        self.provider = provider

    def list_playlists(self) -> List[Dict[str, Any]]:
        """Return list of user playlists with metadata."""
        playlists = self.provider.get_user_playlists()
        result = []
        for pl in playlists:
            result.append({
                "id": pl.id,
                "name": pl.title,
                "title": pl.title,
                "provider": pl.provider,
                "description": pl.description or "",
                "num_tracks": pl.track_count,
                "track_count": pl.track_count,
                "duration_seconds": pl.duration_seconds,
                "public": pl.is_public,
                "last_updated": pl.updated_at,
            })
        return result

    def get_playlist(self, identifier: str) -> Optional[Any]:
        """Find a playlist by its ID or exact name match."""
        return self.provider.get_playlist(identifier)

    def get_tracks(self, identifier: str) -> List[Any]:
        """Fetch all tracks for the specified playlist ID or name."""
        pl = self.get_playlist(identifier)
        if not pl:
            raise ValueError(f"Playlist not found: {identifier}")
        pl_id = getattr(pl, "id", None) or identifier
        return self.provider.get_playlist_tracks(str(pl_id))

    def create_playlist(
        self,
        title: str,
        description: str = "",
        public: bool = False,
        folder: Optional[str] = None,
    ) -> Any:
        """Create a new playlist."""
        return self.provider.create_playlist(title=title, description=description, is_public=public, folder=folder)

    def get_or_create_playlist(
        self,
        title: str,
        description: str = "",
        public: bool = False,
        folder: Optional[str] = None,
    ) -> Any:
        """Find an existing user playlist with matching name or create a new one."""
        existing = self.get_playlist(title)
        if existing:
            if folder:
                pl_id = getattr(existing, "id", None) or title
                self.move_playlist_to_folder(str(pl_id), folder)
            return existing
        return self.create_playlist(title=title, description=description, public=public, folder=folder)

    def move_playlist_to_folder(self, playlist_id: str, folder: str) -> bool:
        """Move a playlist into a folder."""
        return self.provider.move_playlist_to_folder(playlist_id, folder)

    def get_folders(self) -> List[Dict[str, Any]]:
        """Fetch all playlist folders owned by the authenticated user."""
        return self.provider.get_folders()

    def add_tracks(
        self,
        playlist: Any,
        track_ids: List[Union[str, int]],
        position: Optional[int] = None,
    ) -> bool:
        """Add tracks to a playlist."""
        if not track_ids:
            return True
        pl_id = str(getattr(playlist, "id", playlist))
        str_ids = [str(t) for t in track_ids]
        return self.provider.add_tracks_to_playlist(pl_id, str_ids, position=position)

    def export_playlist(self, identifier: str, export_format: str = "json") -> str:
        """Export playlist tracks in json, m3u8, or csv format."""
        pl = self.get_playlist(identifier)
        if not pl:
            raise ValueError(f"Playlist not found: {identifier}")

        pl_id = str(getattr(pl, "id", identifier))
        tracks = self.provider.get_playlist_tracks(pl_id)
        fmt = export_format.lower().strip()

        pl_name = getattr(pl, "title", None) or getattr(pl, "name", "Playlist")

        if fmt == "json":
            data = serialize_playlist(pl, tracks)
            return json.dumps(data, indent=2, ensure_ascii=False)

        elif fmt in ("m3u", "m3u8"):
            lines = ["#EXTM3U", f"#PLAYLIST:{pl_name}"]
            for t in tracks:
                artist = getattr(getattr(t, "artist", None), "name", "Unknown Artist")
                duration = getattr(t, "duration_seconds", None) or getattr(t, "duration", -1)
                t_name = getattr(t, "title", None) or getattr(t, "name", "Unknown")
                lines.append(f"#EXTINF:{duration},{artist} - {t_name}")
                t_id = getattr(t, "id", "")
                listen_url = getattr(t, "listen_url", None)
                if listen_url:
                    lines.append(str(listen_url))
                else:
                    lines.append(f"track://{t_id}")
            return "\n".join(lines)

        elif fmt == "csv":
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(["id", "title", "artist", "album", "duration_seconds", "isrc"])
            for t in tracks:
                artist = getattr(getattr(t, "artist", None), "name", "Unknown Artist")
                alb_obj = getattr(t, "album", None)
                album = getattr(alb_obj, "name", None) or getattr(alb_obj, "title", "Unknown Album") if alb_obj else "Unknown Album"
                dur = getattr(t, "duration_seconds", None) or getattr(t, "duration", 0)
                t_name = getattr(t, "title", None) or getattr(t, "name", "")
                writer.writerow([
                    getattr(t, "id", ""),
                    t_name,
                    artist,
                    album,
                    dur,
                    getattr(t, "isrc", "") or "",
                ])
            return output.getvalue()

        else:
            raise ValueError(f"Unsupported export format: {export_format}. Use json, m3u8, or csv.")

    def backup_favorites_to_playlist(
        self,
        target_playlist: str = "Favorites Backup",
        mode: str = "replace",
        description: str = "Automated backup of favorite/liked tracks",
        folder: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Fetch all favorite tracks from provider and mirror them into a user playlist."""
        favorites = self.provider.get_favorite_tracks()
        playlist = self.get_or_create_playlist(
            title=target_playlist,
            description=description,
            public=False,
            folder=folder,
        )
        pl_id = getattr(playlist, "id", target_playlist)

        all_fav_ids = [str(t.id) for t in favorites if t and t.id]

        if mode == "replace":
            self.provider.clear_playlist(str(pl_id))
            tracks_to_add = all_fav_ids
        else:
            existing_tracks = self.provider.get_playlist_tracks(str(pl_id))
            existing_ids = {str(t.id) for t in existing_tracks if t and t.id}
            tracks_to_add = [tid for tid in all_fav_ids if tid not in existing_ids]

        added = self.add_tracks(playlist=playlist, track_ids=tracks_to_add)

        res: Dict[str, Any] = {
            "success": True,
            "provider": self.provider.name,
            "target_playlist": target_playlist,
            "playlist_id": str(pl_id),
            "mode": mode,
            "total_favorites": len(favorites),
            "tracks_added_count": len(tracks_to_add),
            "tracks_added": added,
        }
        if folder:
            res["folder"] = folder
        return res

    def export_favorites(self, export_format: str = "json") -> str:
        """Export favorite tracks in json, m3u8, or csv format."""
        tracks = self.provider.get_favorite_tracks()
        fmt = export_format.lower().strip()

        if fmt == "json":
            serialized_tracks = [serialize_track(t) for t in tracks]
            data = {
                "title": "Favorite Tracks",
                "provider": self.provider.name,
                "exported_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "track_count": len(tracks),
                "tracks": serialized_tracks,
            }
            return json.dumps(data, indent=2, ensure_ascii=False)

        elif fmt in ("m3u", "m3u8"):
            lines = ["#EXTM3U", "#PLAYLIST:Favorite Tracks"]
            for t in tracks:
                artist = getattr(getattr(t, "artist", None), "name", "Unknown Artist")
                duration = getattr(t, "duration_seconds", None) or getattr(t, "duration", -1)
                t_name = getattr(t, "title", None) or getattr(t, "name", "Unknown")
                lines.append(f"#EXTINF:{duration},{artist} - {t_name}")
                t_id = getattr(t, "id", "")
                listen_url = getattr(t, "listen_url", None)
                if listen_url:
                    lines.append(str(listen_url))
                else:
                    lines.append(f"track://{t_id}")
            return "\n".join(lines)

        elif fmt == "csv":
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(["id", "title", "artist", "album", "duration_seconds", "isrc"])
            for t in tracks:
                artist = getattr(getattr(t, "artist", None), "name", "Unknown Artist")
                alb_obj = getattr(t, "album", None)
                album = getattr(alb_obj, "name", None) or getattr(alb_obj, "title", "Unknown Album") if alb_obj else "Unknown Album"
                dur = getattr(t, "duration_seconds", None) or getattr(t, "duration", 0)
                t_name = getattr(t, "title", None) or getattr(t, "name", "")
                writer.writerow([
                    getattr(t, "id", ""),
                    t_name,
                    artist,
                    album,
                    dur,
                    getattr(t, "isrc", "") or "",
                ])
            return output.getvalue()

        else:
            raise ValueError(f"Unsupported export format: {export_format}. Use json, m3u8, or csv.")



