"""Service for discovering daily mixes, archiving daily discovery, and synthesizing megamixes."""

from __future__ import annotations

import datetime
import logging
import random
import re
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from curo.core.models import CuratedMix, Track
from curo.core.provider import MusicProvider
from curo.services.playlist_service import PlaylistService

logger = logging.getLogger(__name__)


class MixService:
    """Discovers algorithmic mixes, archives discovery mixes, and generates randomized megamixes."""

    def __init__(self, provider: MusicProvider) -> None:
        self.provider = provider
        self.playlist_service = PlaylistService(provider)

    def discover_all_mixes(self) -> List[Any]:
        """Discover all available user mixes from the active provider."""
        return self.provider.get_curated_mixes()

    def get_categorized_daily_mixes(self) -> Tuple[Optional[Any], List[Any]]:
        """Categorize discovered mixes into (discovery_mix, list_of_daily_mixes)."""
        all_mixes = self.discover_all_mixes()
        discovery_mix: Optional[Any] = None
        daily_mixes: List[Any] = []

        for m in all_mixes:
            m_type = str(getattr(m, "mix_type", "")).lower()
            m_title = getattr(m, "title", None) or getattr(m, "name", "") or ""
            title_lower = m_title.lower()

            if "video" in m_type or "video" in title_lower:
                continue

            if "discovery" in m_type or "discovery" in title_lower:
                if not discovery_mix:
                    discovery_mix = m
                continue

            if "daily" in m_type or "daily mix" in title_lower:
                daily_mixes.append(m)

        def _natural_key(m: Any):
            t = getattr(m, "title", None) or getattr(m, "name", "") or ""
            return [int(chunk) if chunk.isdigit() else chunk.lower() for chunk in re.split(r"(\d+)", t)]

        daily_mixes.sort(key=_natural_key)
        return discovery_mix, daily_mixes

    def backup_daily_discovery(
        self,
        playlist_name_format: str = "Discovery Backup - {date}",
        description: str = "Automated daily backup of Daily Discovery",
    ) -> Dict[str, Any]:
        """Archive today's Daily Discovery mix into a permanent dated playlist."""
        today_str = datetime.date.today().isoformat()
        playlist_name = playlist_name_format.format(date=today_str)

        discovery_mix, _ = self.get_categorized_daily_mixes()
        if not discovery_mix:
            raise RuntimeError(f"Could not locate Daily Discovery mix on {self.provider.display_name}.")

        mix_id = getattr(discovery_mix, "id", None)
        tracks = self.provider.get_mix_tracks(str(mix_id)) if mix_id else []
        track_ids = [str(t.id) for t in tracks if getattr(t, "id", None)]

        if not track_ids:
            logger.warning("Daily Discovery mix contained 0 tracks.")
            return {
                "success": False,
                "reason": "Discovery mix had no tracks",
                "playlist_name": playlist_name,
                "track_count": 0,
            }

        playlist = self.playlist_service.get_or_create_playlist(
            title=playlist_name,
            description=f"{description} (archived on {today_str})",
            public=False,
        )

        pl_id = getattr(playlist, "id", playlist_name)
        added = self.playlist_service.add_tracks(
            playlist=playlist,
            track_ids=track_ids,
        )

        return {
            "success": True,
            "provider": self.provider.name,
            "playlist_name": playlist_name,
            "playlist_id": str(pl_id),
            "track_count": len(track_ids),
            "tracks_added": added,
            "date": today_str,
        }

    def create_megamix(
        self,
        target_playlist: str = "Daily Megamix",
        include_discovery: bool = True,
        include_daily_mixes: bool = True,
        shuffle: bool = True,
        mode: str = "replace",
        description: str = "All daily mix tunes in random order - updated on {date}",
        limit: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Collect tracks across all daily mixes, deduplicate, shuffle, and build the Megamix."""
        today_str = datetime.date.today().isoformat()
        formatted_desc = description.format(date=today_str)

        discovery_mix, daily_mixes = self.get_categorized_daily_mixes()

        mixes_to_process: List[Any] = []
        if include_discovery and discovery_mix:
            mixes_to_process.append(discovery_mix)
        if include_daily_mixes:
            mixes_to_process.extend(daily_mixes)

        if not mixes_to_process:
            raise RuntimeError(f"No daily mixes or discovery mix found on {self.provider.display_name}.")

        collected_track_ids: List[str] = []
        seen_ids: Set[str] = set()
        seen_isrcs: Set[str] = set()
        mix_summaries: List[Dict[str, Any]] = []

        for m in mixes_to_process:
            m_title = getattr(m, "title", None) or getattr(m, "name", "Mix")
            mix_id = getattr(m, "id", None)
            try:
                items = self.provider.get_mix_tracks(str(mix_id)) if mix_id else []
            except Exception as e:
                logger.warning(f"Failed to fetch tracks for mix '{m_title}': {e}")
                items = []

            count_from_this_mix = 0
            for item in items:
                raw_id = getattr(item, "id", None)
                if not raw_id:
                    continue
                tid = str(raw_id)
                isrc = getattr(item, "isrc", "") or ""

                if tid in seen_ids:
                    continue
                if isrc and isrc in seen_isrcs:
                    continue

                seen_ids.add(tid)
                if isrc:
                    seen_isrcs.add(isrc)

                collected_track_ids.append(tid)
                count_from_this_mix += 1

            mix_summaries.append({
                "title": m_title,
                "type": str(getattr(m, "mix_type", "")),
                "total_items": len(items),
                "unique_added": count_from_this_mix,
            })

        total_unique = len(collected_track_ids)
        if total_unique == 0:
            return {
                "success": False,
                "reason": "No tracks extracted from mixes",
                "target_playlist": target_playlist,
                "tracks_count": 0,
            }

        if shuffle:
            random.shuffle(collected_track_ids)

        if limit and limit > 0:
            collected_track_ids = collected_track_ids[:limit]

        playlist = self.playlist_service.get_or_create_playlist(
            title=target_playlist,
            description=formatted_desc,
            public=False,
        )

        pl_id = getattr(playlist, "id", target_playlist)
        added = self.playlist_service.add_tracks(
            playlist=playlist,
            track_ids=collected_track_ids,
        )

        return {
            "success": True,
            "provider": self.provider.name,
            "target_playlist": target_playlist,
            "playlist_id": str(pl_id),
            "mode": mode,
            "shuffled": shuffle,
            "mixes_processed": len(mixes_to_process),
            "mix_details": mix_summaries,
            "total_unique_tracks": total_unique,
            "tracks_added": added,
            "date": today_str,
        }

