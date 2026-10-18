"""Service for discovering Tidal daily mixes, archiving daily discovery, and synthesizing megamixes."""

from __future__ import annotations

import datetime
import logging
import random
from typing import Any, Dict, List, Optional, Set, Tuple

import tidalapi
from tidalapi.mix import Mix, MixType
from tidalapi.playlist import UserPlaylist

from tidal_cli.services.playlist_service import PlaylistService

logger = logging.getLogger(__name__)


class MixService:
    """Discovers algorithmic mixes, archives discovery mixes, and generates randomized megamixes."""

    def __init__(self, session: tidalapi.Session) -> None:
        self.session = session
        self.playlist_service = PlaylistService(session)

    def discover_all_mixes(self) -> List[Mix]:
        """Dynamically discover all available user mixes from Tidal.

        Handles any dynamic count of mixes (1, 2, 8+, etc.) by checking:
        1. session.mixes() page categories
        2. session.user.favorites.mixes()
        3. session.home() categories
        """
        discovered: Dict[str, Mix] = {}

        def _is_valid_mix(item: Any) -> bool:
            if not hasattr(item, "id") or not getattr(item, "id", None):
                return False
            return (
                isinstance(item, (Mix, getattr(tidalapi.mix, "MixV2", Mix)))
                or hasattr(item, "mix_type")
                or hasattr(item, "items")
            )

        # 1. Inspect session.mixes()
        try:
            mix_page = self.session.mixes()
            if mix_page and hasattr(mix_page, "categories"):
                for cat in mix_page.categories:
                    items = getattr(cat, "items", []) or []
                    for item in items:
                        if _is_valid_mix(item) and item.id not in discovered:
                            discovered[item.id] = item
        except Exception as e:
            logger.debug(f"session.mixes() query failed or incomplete: {e}")

        # 2. Inspect session.user.favorites.mixes()
        try:
            fav_mixes = self.session.user.favorites.mixes(limit=50)
            for m in fav_mixes:
                m_id = getattr(m, "id", None)
                if m_id and m_id not in discovered:
                    try:
                        full_mix = self.session.mix(m_id)
                        if _is_valid_mix(full_mix):
                            discovered[m_id] = full_mix
                        elif _is_valid_mix(m):
                            discovered[m_id] = m
                    except Exception:
                        if _is_valid_mix(m):
                            discovered[m_id] = m
        except Exception as e:
            logger.debug(f"session.user.favorites.mixes() query failed: {e}")

        # 3. Inspect session.home()
        try:
            home_page = self.session.home()
            if home_page and hasattr(home_page, "categories"):
                for cat in home_page.categories:
                    title = getattr(cat, "title", "").lower()
                    if "mix" in title or "for you" in title or "discovery" in title:
                        items = getattr(cat, "items", []) or []
                        for item in items:
                            if _is_valid_mix(item) and item.id not in discovered:
                                discovered[item.id] = item
        except Exception as e:
            logger.debug(f"session.home() query failed: {e}")

        return list(discovered.values())

    def get_categorized_daily_mixes(self) -> Tuple[Optional[Mix], List[Mix]]:
        """Categorize discovered mixes into (discovery_mix, list_of_daily_mixes).

        NOTE: The count of daily mixes is completely unconstrained and dynamic.
        Tidal generates varying numbers of daily mixes for each user based on
        listening breadth and history (e.g. 1 to 8+ mixes). This function assumes
        no upper or lower limit and captures ALL valid audio daily mixes present.
        """
        all_mixes = self.discover_all_mixes()
        discovery_mix: Optional[Mix] = None
        daily_mixes: List[Mix] = []

        for m in all_mixes:
            m_type = getattr(m, "mix_type", None)
            m_title = getattr(m, "title", "") or ""
            title_lower = m_title.lower()

            # Skip video mixes
            if m_type == getattr(MixType, "video_daily", None) or "video" in title_lower:
                continue

            # Identify My Daily Discovery
            if m_type == MixType.discovery or "discovery" in title_lower:
                if not discovery_mix:
                    discovery_mix = m
                continue

            # Identify any Daily Mix (e.g. Daily Mix 1, Daily Mix 2, ... Daily Mix N)
            # Dynamic matching: captures all daily mix instances without any count limits
            if m_type == MixType.daily or "daily mix" in title_lower:
                daily_mixes.append(m)

        # Sort daily mixes naturally by title (e.g. Daily Mix 1, Daily Mix 2, ... Daily Mix 10)
        import re

        def _natural_key(m: Mix):
            t = getattr(m, "title", "") or ""
            return [int(chunk) if chunk.isdigit() else chunk.lower() for chunk in re.split(r"(\d+)", t)]

        daily_mixes.sort(key=_natural_key)

        return discovery_mix, daily_mixes

    def backup_daily_discovery(
        self,
        playlist_name_format: str = "Discovery Backup - {date}",
        description: str = "Automated daily backup of Tidal Daily Discovery",
    ) -> Dict[str, Any]:
        """Archive today's Daily Discovery mix into a permanent dated playlist."""
        today_str = datetime.date.today().isoformat()
        playlist_name = playlist_name_format.format(date=today_str)

        discovery_mix, _ = self.get_categorized_daily_mixes()
        if not discovery_mix:
            raise RuntimeError("Could not locate Tidal 'My Daily Discovery' mix on this account.")

        tracks = discovery_mix.items()
        track_ids = [t.id for t in tracks if hasattr(t, "id")]

        if not track_ids:
            logger.warning("Daily Discovery mix contained 0 tracks.")
            return {
                "success": False,
                "reason": "Discovery mix had no tracks",
                "playlist_name": playlist_name,
                "track_count": 0,
            }

        # Create or retrieve the dated backup playlist
        playlist = self.playlist_service.get_or_create_playlist(
            title=playlist_name,
            description=f"{description} (archived on {today_str})",
            public=False,
        )

        # Add tracks
        added = self.playlist_service.add_tracks(
            playlist=playlist,
            track_ids=track_ids,
            allow_duplicates=False,
        )

        return {
            "success": True,
            "playlist_name": playlist_name,
            "playlist_id": playlist.id,
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
        mode: str = "replace",  # 'replace' or 'append'
        description: str = "All daily mix tunes in random order - updated on {date}",
        limit: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Collect tracks across all daily mixes, deduplicate, shuffle, and build the Megamix."""
        today_str = datetime.date.today().isoformat()
        formatted_desc = description.format(date=today_str)

        discovery_mix, daily_mixes = self.get_categorized_daily_mixes()

        mixes_to_process: List[Mix] = []
        if include_discovery and discovery_mix:
            mixes_to_process.append(discovery_mix)
        if include_daily_mixes:
            mixes_to_process.extend(daily_mixes)

        if not mixes_to_process:
            raise RuntimeError("No daily mixes or discovery mix found on this account.")

        logger.info(
            f"Found {len(mixes_to_process)} total mixes to aggregate: "
            f"{[getattr(m, 'title', m.id) for m in mixes_to_process]}"
        )

        # Aggregate tracks with deduplication by track ID and ISRC
        collected_track_ids: List[Union[str, int]] = []
        seen_ids: Set[str] = set()
        seen_isrcs: Set[str] = set()
        mix_summaries: List[Dict[str, Any]] = []

        for m in mixes_to_process:
            m_title = getattr(m, "title", "Unknown Mix")
            try:
                items = m.items()
            except Exception as e:
                logger.warning(f"Failed to fetch tracks for mix '{m_title}': {e}")
                items = []

            count_from_this_mix = 0
            for item in items:
                if not hasattr(item, "id"):
                    continue
                tid = str(item.id)
                isrc = getattr(item, "isrc", "") or ""

                # Deduplication check
                if tid in seen_ids:
                    continue
                if isrc and isrc in seen_isrcs:
                    continue

                seen_ids.add(tid)
                if isrc:
                    seen_isrcs.add(isrc)

                collected_track_ids.append(item.id)
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

        # Apply random shuffle if requested
        if shuffle:
            random.shuffle(collected_track_ids)

        # Apply limit if requested
        if limit and limit > 0:
            collected_track_ids = collected_track_ids[:limit]

        # Get or create target playlist
        playlist = self.playlist_service.get_or_create_playlist(
            title=target_playlist,
            description=formatted_desc,
            public=False,
        )

        # Handle 'replace' vs 'append'
        if mode == "replace":
            logger.info(f"Clearing previous tracks in '{target_playlist}'...")
            self.playlist_service.clear_playlist(playlist)

        # Add shuffled tracks in safe batches
        added = self.playlist_service.add_tracks(
            playlist=playlist,
            track_ids=collected_track_ids,
            allow_duplicates=False,
        )

        return {
            "success": True,
            "target_playlist": target_playlist,
            "playlist_id": playlist.id,
            "mode": mode,
            "shuffled": shuffle,
            "mixes_processed": len(mixes_to_process),
            "mix_details": mix_summaries,
            "total_unique_tracks": total_unique,
            "tracks_added": added,
            "date": today_str,
        }
