"""Service for inspecting and exporting Tidal albums with full metadata."""

from __future__ import annotations

import csv
import io
import json
import logging
from typing import Any, Dict, List, Optional

import tidalapi
from tidalapi.album import Album
from tidalapi.media import Track

from tidal_cli.services.metadata_serializer import serialize_album

logger = logging.getLogger(__name__)


class AlbumService:
    """Provides operations for inspecting and exporting albums."""

    def __init__(self, session: tidalapi.Session) -> None:
        self.session = session

    def get_album(self, identifier: str) -> Optional[Album]:
        """Find an album by its ID or title search."""
        ident_str = str(identifier).strip()

        # 1. Try direct ID lookup if numeric or standard ID
        if ident_str.isdigit() or len(ident_str) > 0:
            try:
                alb = self.session.album(ident_str)
                if alb and getattr(alb, "id", None):
                    # Call alb.name to trigger lazy load / verification
                    _ = alb.name
                    return alb
            except Exception:
                pass

        # 2. Try searching by query
        try:
            results = self.session.search(ident_str, models=[Album], limit=10)
            albums = results.get("albums", []) if isinstance(results, dict) else getattr(results, "albums", [])
            if albums:
                # Prioritize exact title match (case-insensitive)
                for a in albums:
                    if getattr(a, "name", "").lower() == ident_str.lower():
                        return a
                return albums[0]
        except Exception as e:
            logger.debug(f"Album search for '{ident_str}' failed: {e}")

        return None

    def get_tracks(self, identifier: str) -> List[Track]:
        """Fetch all tracks for the specified album ID or title."""
        alb = self.get_album(identifier)
        if not alb:
            raise ValueError(f"Album not found: {identifier}")
        return alb.tracks()

    def export_album(self, identifier: str, export_format: str = "json") -> str:
        """Export album with full metadata in json, m3u8, or csv format."""
        alb = self.get_album(identifier)
        if not alb:
            raise ValueError(f"Album not found: {identifier}")

        tracks = alb.tracks()
        fmt = export_format.lower().strip()

        if fmt == "json":
            data = serialize_album(alb, tracks)
            return json.dumps(data, indent=2, ensure_ascii=False)

        elif fmt in ("m3u", "m3u8"):
            lines = ["#EXTM3U", f"#PLAYLIST:{alb.name}"]
            for t in tracks:
                artist_name = getattr(getattr(t, "artist", None), "name", "Unknown Artist")
                duration = getattr(t, "duration", -1)
                lines.append(f"#EXTINF:{duration},{artist_name} - {t.name}")
                lines.append(f"https://tidal.com/browse/track/{t.id}")
            return "\n".join(lines)

        elif fmt == "csv":
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow([
                "track_num",
                "volume_num",
                "id",
                "title",
                "artist",
                "album",
                "duration_seconds",
                "isrc",
                "explicit",
                "audio_quality",
            ])
            for t in tracks:
                artist_name = getattr(getattr(t, "artist", None), "name", "Unknown Artist")
                writer.writerow([
                    getattr(t, "track_num", ""),
                    getattr(t, "volume_num", ""),
                    t.id,
                    t.name,
                    artist_name,
                    alb.name,
                    getattr(t, "duration", 0),
                    getattr(t, "isrc", ""),
                    getattr(t, "explicit", False),
                    str(getattr(t, "audio_quality", "")),
                ])
            return output.getvalue()

        else:
            raise ValueError(f"Unsupported export format: {export_format}. Use json, m3u8, or csv.")

