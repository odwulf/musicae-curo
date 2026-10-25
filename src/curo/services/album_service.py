"""Service for inspecting and exporting albums with full metadata."""

from __future__ import annotations

import csv
import io
import json
import logging
from typing import Any, Dict, List, Optional

from curo.core.provider import MusicProvider
from curo.services.metadata_serializer import serialize_album

logger = logging.getLogger(__name__)


class AlbumService:
    """Provides operations for inspecting and exporting albums across providers."""

    def __init__(self, provider: MusicProvider) -> None:
        self.provider = provider

    def get_album(self, identifier: str) -> Optional[Any]:
        """Find an album by its ID or title search."""
        ident_str = str(identifier).strip()
        alb = self.provider.get_album(ident_str)
        if alb:
            return alb
        # Fallback to search if provider supports searching albums or tracks
        return None

    def get_tracks(self, identifier: str) -> List[Any]:
        """Fetch all tracks for the specified album ID or title."""
        alb = self.get_album(identifier)
        if not alb:
            raise ValueError(f"Album not found: {identifier}")
        alb_id = getattr(alb, "id", identifier)
        return self.provider.get_album_tracks(str(alb_id))

    def export_album(self, identifier: str, export_format: str = "json") -> str:
        """Export album with full metadata in json, m3u8, or csv format."""
        alb = self.get_album(identifier)
        if not alb:
            raise ValueError(f"Album not found: {identifier}")

        alb_id = getattr(alb, "id", identifier)
        tracks = self.provider.get_album_tracks(str(alb_id))
        fmt = export_format.lower().strip()

        alb_name = getattr(alb, "title", None) or getattr(alb, "name", "Album")

        if fmt == "json":
            data = serialize_album(alb, tracks)
            return json.dumps(data, indent=2, ensure_ascii=False)

        elif fmt in ("m3u", "m3u8"):
            lines = ["#EXTM3U", f"#PLAYLIST:{alb_name}"]
            for t in tracks:
                artist_name = getattr(getattr(t, "artist", None), "name", "Unknown Artist")
                duration = getattr(t, "duration_seconds", None) or getattr(t, "duration", -1)
                t_name = getattr(t, "title", None) or getattr(t, "name", "Unknown")
                lines.append(f"#EXTINF:{duration},{artist_name} - {t_name}")
                t_id = getattr(t, "id", "")
                lines.append(f"track://{t_id}")
            return "\n".join(lines)

        elif fmt == "csv":
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow([
                "id",
                "title",
                "artist",
                "album",
                "duration_seconds",
                "isrc",
            ])
            for t in tracks:
                artist_name = getattr(getattr(t, "artist", None), "name", "Unknown Artist")
                dur = getattr(t, "duration_seconds", None) or getattr(t, "duration", 0)
                t_name = getattr(t, "title", None) or getattr(t, "name", "")
                writer.writerow([
                    getattr(t, "id", ""),
                    t_name,
                    artist_name,
                    alb_name,
                    dur,
                    getattr(t, "isrc", ""),
                ])
            return output.getvalue()

        else:
            raise ValueError(f"Unsupported export format: {export_format}. Use json, m3u8, or csv.")

