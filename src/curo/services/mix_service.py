"""Service for discovering daily mixes, archiving daily discovery, and synthesizing megamixes."""

from __future__ import annotations

import datetime
import hashlib
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


def _is_mock(val: Any) -> bool:
    """Helper to detect mock objects in unit tests."""
    if val is None:
        return False
    return "Mock" in type(val).__name__


def _clean_str(val: Any) -> str:
    """Helper to sanitize mock attributes into clean strings."""
    if _is_mock(val) or val is None:
        return ""
    return str(val)


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
        self.mixes_dir = self.config.mixes_dir

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
        folder: Optional[str] = None,
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
            res = {
                "success": False,
                "reason": "Discovery mix had no tracks",
                "playlist_name": playlist_name,
                "track_count": 0,
            }
            if folder:
                res["folder"] = folder
            return res

        playlist = self.playlist_service.get_or_create_playlist(
            title=playlist_name,
            description=f"{description} (archived on {today_str})",
            public=False,
            folder=folder,
        )

        pl_id = getattr(playlist, "id", playlist_name)

        # Idempotence: check existing tracks in playlist to avoid duplicate appends on re-run
        try:
            existing_tracks = self.provider.get_playlist_tracks(str(pl_id))
            existing_ids = [str(t.id) for t in existing_tracks if getattr(t, "id", None)]
            if existing_ids == track_ids:
                logger.info(f"Playlist '{playlist_name}' already contains identical tracks; skipping append.")
                res = {
                    "success": True,
                    "provider": self.provider.name,
                    "playlist_name": playlist_name,
                    "playlist_id": str(pl_id),
                    "track_count": len(track_ids),
                    "tracks_added": 0,
                    "date": today_str,
                    "skipped": True,
                    "reason": "Playlist already contains identical tracks",
                }
                if folder:
                    res["folder"] = folder
                return res
        except Exception as e:
            logger.debug(f"Could not verify existing tracks in '{playlist_name}': {e}")

        added = self.playlist_service.add_tracks(
            playlist=playlist,
            track_ids=track_ids,
        )

        # Record discovery tracks into history for rolling exclusions
        if track_ids:
            disc_isrcs = [str(getattr(t, "isrc", "")) for t in tracks if getattr(t, "isrc", None)]
            self._record_daily_history(track_ids, disc_isrcs)

        res = {
            "success": True,
            "provider": self.provider.name,
            "playlist_name": playlist_name,
            "playlist_id": str(pl_id),
            "track_count": len(track_ids),
            "tracks_added": added,
            "date": today_str,
        }
        if folder:
            res["folder"] = folder
        return res

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
        folder: Optional[str] = None,
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
            msg = "No tracks extracted from mixes" if not excluded_recent_count else f"All {excluded_recent_count} candidate tracks were excluded by the {exclude_recent_days}-day history filter"
            logger.info(f"Skipping Megamix '{target_playlist}': {msg}")
            return {
                "success": True,
                "skipped": True,
                "reason": msg,
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
            folder=folder,
        )

        pl_id = getattr(playlist, "id", target_playlist)

        if mode == "replace":
            self.provider.clear_playlist(str(pl_id))

        added = self.playlist_service.add_tracks(
            playlist=playlist,
            track_ids=collected_track_ids,
        )

        res = {
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
        if folder:
            res["folder"] = folder
        return res

    # --- Hourly Sync & Mix Change Detection ---

    def _get_mix_slug(self, mix: Any) -> str:
        """Derive a safe filesystem slug for a mix."""
        m_title = getattr(mix, "title", None) or getattr(mix, "name", "") or "mix"
        slug = re.sub(r"[^\w\-]+", "_", m_title.strip().lower()).strip("_")
        return slug or "mix"

    def _compute_mix_hash(self, track_ids: List[str]) -> str:
        """Compute stable SHA-256 hash of ordered track IDs."""
        clean_ids = [_clean_str(tid) for tid in track_ids if _clean_str(tid)]
        return hashlib.sha256(",".join(clean_ids).encode("utf-8")).hexdigest()

    def _serialize_track_dict(self, t: Any) -> Dict[str, Any]:
        """Convert a Track object or mock into a clean JSON-serializable dictionary."""
        t_id = _clean_str(getattr(t, "id", ""))
        title = _clean_str(getattr(t, "title", None) or getattr(t, "name", ""))
        artist_obj = getattr(t, "artist", None)
        artist_name = _clean_str(getattr(artist_obj, "name", "")) if not _is_mock(artist_obj) else ""
        album_obj = getattr(t, "album", None)
        album_title = _clean_str(getattr(album_obj, "title", "")) if not _is_mock(album_obj) else ""
        isrc = _clean_str(getattr(t, "isrc", ""))
        duration = getattr(t, "duration", None)
        if _is_mock(duration):
            duration = None
        return {
            "id": t_id,
            "title": title,
            "artist": artist_name,
            "album": album_title,
            "isrc": isrc,
            "duration": duration,
        }

    def get_cached_mix_json(self, mix_slug: str) -> Optional[Dict[str, Any]]:
        """Read cached mix JSON from ~/.config/curo/mixes/{slug}.json."""
        file_path = self.mixes_dir / f"{mix_slug}.json"
        if file_path.exists():
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Failed to read cached mix JSON from {file_path}: {e}")
        return None

    def save_cached_mix_json(self, mix_slug: str, data: Dict[str, Any]) -> None:
        """Write mix JSON to ~/.config/curo/mixes/{slug}.json."""
        self.config.ensure_config_dir()
        file_path = self.mixes_dir / f"{mix_slug}.json"
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            self.config.secure_file(file_path)
        except Exception as e:
            logger.warning(f"Failed to save cached mix JSON to {file_path}: {e}")

    def sync_mixes(
        self,
        folder: Optional[str] = "Discovery Backups",
        backup_discovery: bool = True,
        update_megamixes: bool = True,
    ) -> Dict[str, Any]:
        """Detect updates to Daily Discovery and Daily Mixes.

        When a change is detected:
        1. If Daily Discovery changed, archive the PRECEDING period's cached tracks into a dated
           playlist (e.g. change on Wednesday archives Tuesday's mix).
        2. Update the mix's cached JSON file only if that mix actually changed.
        3. If any Daily Mix changed (and update_megamixes is True), regenerate Daily Megamix and
           Daily Fresh Megamix.
        """
        now = datetime.datetime.now()
        today_date = now.date().isoformat()
        now_iso = now.isoformat()

        discovery_mix, daily_mixes = self.get_categorized_daily_mixes()
        results: Dict[str, Any] = {
            "success": True,
            "timestamp": now_iso,
            "mixes_checked": 0,
            "mixes_updated": [],
            "discovery_backup": None,
            "megamixes_updated": False,
            "megamix_result": None,
            "fresh_megamix_result": None,
        }

        # 1. Inspect Daily Discovery
        if discovery_mix:
            results["mixes_checked"] += 1
            disc_slug = self._get_mix_slug(discovery_mix)
            disc_id = str(getattr(discovery_mix, "id", ""))
            disc_tracks = self.provider.get_mix_tracks(disc_id) if disc_id else []
            disc_track_ids = [str(t.id) for t in disc_tracks if getattr(t, "id", None)]
            disc_hash = self._compute_mix_hash(disc_track_ids)

            cached_disc = self.get_cached_mix_json(disc_slug)

            if cached_disc is None:
                # First run: record initial baseline without creating preceding backup
                logger.info(f"Establishing baseline cache for Daily Discovery ({len(disc_track_ids)} tracks)")
                disc_cache_data = {
                    "id": disc_id,
                    "title": _clean_str(getattr(discovery_mix, "title", "Daily Discovery")),
                    "mix_type": _clean_str(getattr(discovery_mix, "mix_type", "discovery_mix")),
                    "last_updated_at": now_iso,
                    "last_updated_date": today_date,
                    "content_hash": disc_hash,
                    "track_count": len(disc_track_ids),
                    "tracks": [self._serialize_track_dict(t) for t in disc_tracks if getattr(t, "id", None)],
                }
                self.save_cached_mix_json(disc_slug, disc_cache_data)
                results["mixes_updated"].append(disc_slug)
            elif cached_disc.get("content_hash") != disc_hash and disc_track_ids:
                logger.info(f"Change detected in Daily Discovery! (hash {cached_disc.get('content_hash')[:8]} -> {disc_hash[:8]})")

                # Archive PRECEDING discovery mix tracks
                preceding_date = cached_disc.get("last_updated_date")
                if not preceding_date:
                    try:
                        preceding_date = datetime.date.fromisoformat(cached_disc.get("last_updated_at", "")[:10]).isoformat()
                    except Exception:
                        preceding_date = (now.date() - datetime.timedelta(days=1)).isoformat()

                if backup_discovery:
                    preceding_tracks = cached_disc.get("tracks", [])
                    preceding_track_ids = [str(t["id"]) for t in preceding_tracks if t.get("id")]
                    if preceding_track_ids:
                        backup_playlist_name = f"Discovery Backup - {preceding_date}"
                        logger.info(f"Archiving preceding Daily Discovery into '{backup_playlist_name}' ({len(preceding_track_ids)} tracks)")
                        pl = self.playlist_service.get_or_create_playlist(
                            title=backup_playlist_name,
                            description=f"Automated backup of Daily Discovery for period {preceding_date}",
                            public=False,
                            folder=folder,
                        )
                        # Avoid duplicate appends on re-run
                        try:
                            existing_tracks = self.provider.get_playlist_tracks(str(pl.id))
                            existing_ids = [str(t.id) for t in existing_tracks if getattr(t, "id", None)]
                            if existing_ids != preceding_track_ids:
                                added = self.playlist_service.add_tracks(playlist=pl, track_ids=preceding_track_ids)
                            else:
                                added = 0
                        except Exception:
                            added = self.playlist_service.add_tracks(playlist=pl, track_ids=preceding_track_ids)

                        results["discovery_backup"] = {
                            "playlist_name": backup_playlist_name,
                            "date": preceding_date,
                            "tracks_added": added,
                        }

                # Update Daily Discovery cache to current tracks
                disc_cache_data = {
                    "id": disc_id,
                    "title": _clean_str(getattr(discovery_mix, "title", "Daily Discovery")),
                    "mix_type": _clean_str(getattr(discovery_mix, "mix_type", "discovery_mix")),
                    "last_updated_at": now_iso,
                    "last_updated_date": today_date,
                    "content_hash": disc_hash,
                    "track_count": len(disc_track_ids),
                    "tracks": [self._serialize_track_dict(t) for t in disc_tracks if getattr(t, "id", None)],
                }
                self.save_cached_mix_json(disc_slug, disc_cache_data)
                results["mixes_updated"].append(disc_slug)

        # 2. Inspect Daily Mixes (1..N)
        daily_mix_changed = False
        for dm in daily_mixes:
            results["mixes_checked"] += 1
            dm_slug = self._get_mix_slug(dm)
            dm_id = str(getattr(dm, "id", ""))
            dm_tracks = self.provider.get_mix_tracks(dm_id) if dm_id else []
            dm_track_ids = [str(t.id) for t in dm_tracks if getattr(t, "id", None)]
            dm_hash = self._compute_mix_hash(dm_track_ids)

            cached_dm = self.get_cached_mix_json(dm_slug)

            if cached_dm is None:
                # Baseline cache
                dm_cache_data = {
                    "id": dm_id,
                    "title": _clean_str(getattr(dm, "title", dm_slug)),
                    "mix_type": _clean_str(getattr(dm, "mix_type", "daily_mix")),
                    "last_updated_at": now_iso,
                    "last_updated_date": today_date,
                    "content_hash": dm_hash,
                    "track_count": len(dm_track_ids),
                    "tracks": [self._serialize_track_dict(t) for t in dm_tracks if getattr(t, "id", None)],
                }
                self.save_cached_mix_json(dm_slug, dm_cache_data)
                results["mixes_updated"].append(dm_slug)
                daily_mix_changed = True
            elif cached_dm.get("content_hash") != dm_hash and dm_track_ids:
                logger.info(f"Change detected in {dm_slug}! (hash {cached_dm.get('content_hash')[:8]} -> {dm_hash[:8]})")
                daily_mix_changed = True
                dm_cache_data = {
                    "id": dm_id,
                    "title": _clean_str(getattr(dm, "title", dm_slug)),
                    "mix_type": _clean_str(getattr(dm, "mix_type", "daily_mix")),
                    "last_updated_at": now_iso,
                    "last_updated_date": today_date,
                    "content_hash": dm_hash,
                    "track_count": len(dm_track_ids),
                    "tracks": [self._serialize_track_dict(t) for t in dm_tracks if getattr(t, "id", None)],
                }
                self.save_cached_mix_json(dm_slug, dm_cache_data)
                results["mixes_updated"].append(dm_slug)

        # 3. Regenerate Megamixes if any Daily Mix changed
        if update_megamixes and daily_mix_changed:
            logger.info("Daily Mix updates detected; regenerating Daily Megamix and Daily Fresh Megamix...")
            results["megamixes_updated"] = True
            results["megamix_result"] = self.create_megamix(
                target_playlist="Daily Megamix",
                include_discovery=False,
                include_daily_mixes=True,
                shuffle=True,
                mode="replace",
                exclude_recent_days=0,
            )
            results["fresh_megamix_result"] = self.create_megamix(
                target_playlist="Daily Fresh Megamix",
                include_discovery=False,
                include_daily_mixes=True,
                shuffle=True,
                mode="replace",
                exclude_recent_days=7,
            )

        return results

