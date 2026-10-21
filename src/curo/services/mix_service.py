"""Service for discovering daily mixes, archiving daily discovery, and synthesizing megamixes."""

from __future__ import annotations

import datetime
import json
import logging
from pathlib import Path
import random
import re
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from curo.config import ConfigManager, config_manager
from curo.core.models import CuratedMix, Track
from curo.core.provider import MusicProvider
from curo.services.playlist_service import PlaylistService

logger = logging.getLogger(__name__)


class MixService:
    """Discovers algorithmic mixes, archives discovery mixes, and generates randomized megamixes."""

    def __init__(
        self,
        provider: MusicProvider,
        cfg: Optional[ConfigManager] = None,
    ) -> None:
        self.provider = provider
        self.config = cfg or config_manager
        self.playlist_service = PlaylistService(provider)
        self.history_file = self.config.megamix_history_file

    def _load_history(self) -> Dict[str, Any]:
        """Load megamix track history for rolling exclusions."""
        if self.history_file.exists():
            try:
                with open(self.history_file, "r", encoding="utf-8") as f:
                    return json.load(f) or {}
            except Exception as e:
                logger.warning(f"Failed to read megamix history at {self.history_file}: {e}")
        return {}

    def _save_history(self, history: Dict[str, Any]) -> None:
        """Persist megamix track history with secure permissions."""
        try:
            self.config.ensure_config_dir()
            with open(self.history_file, "w", encoding="utf-8") as f:
                json.dump(history, f, indent=2)
            self.config.secure_file(self.history_file)
        except Exception as e:
            logger.warning(f"Failed to save megamix history at {self.history_file}: {e}")

    def _get_recent_excluded_identifiers(self, days: int) -> Tuple[Set[str], Set[str]]:
        """Return (excluded_track_ids, excluded_isrcs) seen in the last `days` days."""
        if days <= 0:
            return set(), set()

        history = self._load_history()
        prov_hist = history.get(self.provider.name, {})

        excluded_ids: Set[str] = set()
        excluded_isrcs: Set[str] = set()

        today = datetime.date.today()
        cutoff = today - datetime.timedelta(days=days)

        for date_str, data in prov_hist.items():
            try:
                d = datetime.date.fromisoformat(date_str)
            except ValueError:
                continue

            if cutoff <= d < today:
                if isinstance(data, dict):
                    excluded_ids.update(str(x) for x in data.get("track_ids", []))
                    excluded_isrcs.update(str(x) for x in data.get("isrcs", []) if x)
                elif isinstance(data, list):
                    excluded_ids.update(str(x) for x in data)

        # Fallback for first run: if history file is empty, check recent Discovery Backup playlists
        if not excluded_ids:
            try:
                user_playlists = self.provider.get_user_playlists()
                for pl in user_playlists:
                    m = re.match(r"Discovery Backup - (\d{4}-\d{2}-\d{2})", pl.title)
                    if m:
                        try:
                            pl_date = datetime.date.fromisoformat(m.group(1))
                            if cutoff <= pl_date < today:
                                pl_tracks = self.provider.get_playlist_tracks(pl.id)
                                for t in pl_tracks:
                                    if t.id:
                                        excluded_ids.add(str(t.id))
                                    if t.isrc:
                                        excluded_isrcs.add(str(t.isrc))
                        except Exception:
                            pass
            except Exception as e:
                logger.debug(f"Could not inspect discovery backups for history fallback: {e}")

        return excluded_ids, excluded_isrcs

    def _record_daily_history(self, track_ids: List[str], isrcs: List[str]) -> None:
        """Record today's tracks into history for future rolling exclusions."""
        today_str = datetime.date.today().isoformat()
        history = self._load_history()
        prov_hist = history.setdefault(self.provider.name, {})

        entry = prov_hist.setdefault(today_str, {"track_ids": [], "isrcs": []})
        existing_ids = set(entry.get("track_ids", []))
        existing_isrcs = set(entry.get("isrcs", []))

        for tid in track_ids:
            if tid not in existing_ids:
                entry["track_ids"].append(tid)
                existing_ids.add(tid)

        for isrc in isrcs:
            if isrc and isrc not in existing_isrcs:
                entry["isrcs"].append(isrc)
                existing_isrcs.add(isrc)

        # Prune entries older than 60 days
        cutoff = datetime.date.today() - datetime.timedelta(days=60)
        to_delete = []
        for d_str in prov_hist.keys():
            try:
                if datetime.date.fromisoformat(d_str) < cutoff:
                    to_delete.append(d_str)
            except ValueError:
                pass
        for d_str in to_delete:
            del prov_hist[d_str]

        self._save_history(history)

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

        # Record discovery tracks into history for rolling exclusions
        if track_ids:
            disc_isrcs = [str(getattr(t, "isrc", "")) for t in tracks if getattr(t, "isrc", None)]
            self._record_daily_history(track_ids, disc_isrcs)

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
        exclude_recent_days: int = 0,
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

        recent_excluded_ids: Set[str] = set()
        recent_excluded_isrcs: Set[str] = set()
        if exclude_recent_days > 0:
            recent_excluded_ids, recent_excluded_isrcs = self._get_recent_excluded_identifiers(exclude_recent_days)

        all_today_track_ids: List[str] = []
        all_today_isrcs: List[str] = []
        collected_track_ids: List[str] = []
        seen_ids: Set[str] = set()
        seen_isrcs: Set[str] = set()
        mix_summaries: List[Dict[str, Any]] = []
        excluded_recent_count: int = 0

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

                all_today_track_ids.append(tid)
                if isrc:
                    all_today_isrcs.append(isrc)

                if tid in seen_ids:
                    continue
                if isrc and isrc in seen_isrcs:
                    continue

                seen_ids.add(tid)
                if isrc:
                    seen_isrcs.add(isrc)

                if exclude_recent_days > 0:
                    if tid in recent_excluded_ids or (isrc and isrc in recent_excluded_isrcs):
                        excluded_recent_count += 1
                        continue

                collected_track_ids.append(tid)
                count_from_this_mix += 1

            mix_summaries.append({
                "title": m_title,
                "type": str(getattr(m, "mix_type", "")),
                "total_items": len(items),
                "unique_added": count_from_this_mix,
            })

        # Record all of today's observed daily mix tracks into history
        if all_today_track_ids:
            self._record_daily_history(all_today_track_ids, all_today_isrcs)

        total_unique = len(collected_track_ids)
        if total_unique == 0:
            return {
                "success": False,
                "reason": "No tracks extracted from mixes" if not excluded_recent_count else f"All {excluded_recent_count} candidate tracks were excluded by the {exclude_recent_days}-day history filter",
                "target_playlist": target_playlist,
                "total_unique_tracks": 0,
                "tracks_count": 0,
                "exclude_recent_days": exclude_recent_days,
                "excluded_recent_repeats": excluded_recent_count,
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

        if mode == "replace":
            self.provider.clear_playlist(str(pl_id))

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
            "exclude_recent_days": exclude_recent_days,
            "excluded_recent_repeats": excluded_recent_count,
            "mixes_processed": len(mixes_to_process),
            "mix_details": mix_summaries,
            "total_unique_tracks": total_unique,
            "tracks_added": added,
            "date": today_str,
        }

