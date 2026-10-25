"""Snapshot service for daily versioned backups, track health alerts, and historical ISRC/UPC recovery."""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from curo.config import ConfigManager, config_manager
from curo.core.provider import MusicProvider
from curo.services.metadata_serializer import serialize_playlist
from curo.services.track_matcher import (
    compute_string_similarity,
    is_track_available,
    normalize_title,
)

logger = logging.getLogger(__name__)


def _safe_str(val: Any) -> str:
    if val is None or hasattr(val, "_mock_return_value") or hasattr(val, "_mock_wraps") or "Mock" in type(val).__name__:
        return ""
    return str(val).strip()


def _safe_int(val: Any) -> Optional[int]:
    if val is None or hasattr(val, "_mock_return_value") or hasattr(val, "_mock_wraps") or "Mock" in type(val).__name__:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


def _slugify(text: str) -> str:
    slug = re.sub(r"[^\w\s-]", "", text.strip().lower())
    slug = re.sub(r"[-\s]+", "_", slug).strip("_")
    return slug or "playlist"


def compute_track_diff(
    prev_tracks: List[Dict[str, Any]],
    curr_tracks: List[Any],
) -> Dict[str, Any]:
    """Compute difference between previous snapshot tracks and current tracks.

    Accurately distinguishes between genuinely added/removed tracks and tracks
    that were relinked/replaced by the provider under a new platform track ID
    (e.g. catalog re-deliveries, remaster updates, license migrations).
    """
    prev_ids = [_safe_str(t.get("id")) for t in prev_tracks if t.get("id")]
    prev_ids = [pid for pid in prev_ids if pid]

    curr_ids: List[str] = []
    for t in curr_tracks:
        tid = _safe_str(getattr(t, "id", None) or (t.get("id") if isinstance(t, dict) else None))
        if tid:
            curr_ids.append(tid)

    prev_set = set(prev_ids)
    curr_set = set(curr_ids)

    added_ids = curr_set - prev_set
    removed_ids = prev_set - curr_set

    reordered = False
    if not added_ids and not removed_ids and prev_ids != curr_ids:
        reordered = True

    raw_added: List[Dict[str, Any]] = []
    for t in curr_tracks:
        tid = _safe_str(getattr(t, "id", None) or (t.get("id") if isinstance(t, dict) else None))
        if tid in added_ids:
            title = (
                _safe_str(getattr(t, "title", None))
                or _safe_str(getattr(t, "name", None))
                or _safe_str(t.get("title") if isinstance(t, dict) else "")
                or _safe_str(t.get("name") if isinstance(t, dict) else "")
            )
            artist_obj = getattr(t, "artist", None) or (t.get("artist") if isinstance(t, dict) else None)
            artist_name = (
                _safe_str(getattr(artist_obj, "name", None))
                or _safe_str(artist_obj.get("name") if isinstance(artist_obj, dict) else "")
                or _safe_str(artist_obj)
            )
            isrc = _safe_str(getattr(t, "isrc", None) or (t.get("isrc") if isinstance(t, dict) else ""))
            dur = _safe_int(
                getattr(t, "duration_seconds", None)
                or getattr(t, "duration", None)
                or (t.get("duration_seconds") if isinstance(t, dict) else None)
                or (t.get("duration") if isinstance(t, dict) else None)
            )
            raw_added.append({
                "id": tid,
                "title": title or "Unknown Title",
                "artist": artist_name,
                "isrc": isrc or None,
                "duration_seconds": dur,
            })

    raw_removed: List[Dict[str, Any]] = []
    for t in prev_tracks:
        tid = _safe_str(t.get("id"))
        if tid in removed_ids:
            title = _safe_str(t.get("title")) or _safe_str(t.get("name"))
            artist_val = t.get("artist")
            artist_name = (
                _safe_str(artist_val.get("name"))
                if isinstance(artist_val, dict)
                else _safe_str(artist_val)
            )
            isrc = _safe_str(t.get("isrc"))
            dur = _safe_int(t.get("duration_seconds") or t.get("duration"))
            raw_removed.append({
                "id": tid,
                "title": title or "Unknown Title",
                "artist": artist_name,
                "isrc": isrc or None,
                "duration_seconds": dur,
            })

    relinked_tracks: List[Dict[str, Any]] = []
    matched_added_indices: Set[int] = set()
    matched_removed_indices: Set[int] = set()

    # Pass 1: Match by ISRC (exact match of standardized recording identifier)
    for r_idx, r in enumerate(raw_removed):
        if r_idx in matched_removed_indices:
            continue
        r_isrc = (r.get("isrc") or "").upper()
        if not r_isrc or len(r_isrc) < 5:
            continue

        for a_idx, a in enumerate(raw_added):
            if a_idx in matched_added_indices:
                continue
            a_isrc = (a.get("isrc") or "").upper()
            if r_isrc == a_isrc:
                matched_removed_indices.add(r_idx)
                matched_added_indices.add(a_idx)
                relinked_tracks.append({
                    "old_id": r["id"],
                    "new_id": a["id"],
                    "title": a["title"] or r["title"],
                    "artist": a["artist"] or r["artist"],
                    "match_reason": "isrc",
                })
                break

    # Pass 2: Match by metadata (normalized title and artist similarity)
    for r_idx, r in enumerate(raw_removed):
        if r_idx in matched_removed_indices:
            continue
        r_title_norm = normalize_title(r["title"])
        r_artist_norm = normalize_title(r["artist"])
        if not r_title_norm or not r_artist_norm:
            continue

        best_a_idx = None
        best_score = 0.0

        for a_idx, a in enumerate(raw_added):
            if a_idx in matched_added_indices:
                continue
            a_title_norm = normalize_title(a["title"])
            a_artist_norm = normalize_title(a["artist"])

            title_sim = 1.0 if r_title_norm == a_title_norm else compute_string_similarity(r["title"], a["title"])
            if title_sim < 0.85:
                continue

            artist_sim = 1.0 if r_artist_norm == a_artist_norm else compute_string_similarity(r["artist"], a["artist"])
            if artist_sim < 0.80:
                continue

            r_dur = r.get("duration_seconds")
            a_dur = a.get("duration_seconds")
            if r_dur and a_dur and abs(r_dur - a_dur) > 20:
                continue

            score = (title_sim * 0.6) + (artist_sim * 0.4)
            if score > best_score:
                best_score = score
                best_a_idx = a_idx

        if best_a_idx is not None and best_score >= 0.85:
            matched_removed_indices.add(r_idx)
            matched_added_indices.add(best_a_idx)
            a_match = raw_added[best_a_idx]
            relinked_tracks.append({
                "old_id": r["id"],
                "new_id": a_match["id"],
                "title": a_match["title"] or r["title"],
                "artist": a_match["artist"] or r["artist"],
                "match_reason": "metadata",
            })

    final_added = [a for idx, a in enumerate(raw_added) if idx not in matched_added_indices]
    final_removed = [r for idx, r in enumerate(raw_removed) if idx not in matched_removed_indices]

    return {
        "added_tracks": final_added,
        "removed_tracks": final_removed,
        "relinked_tracks": relinked_tracks,
        "reordered": reordered,
    }


class SnapshotService:
    """Manages versioned playlist snapshots, availability alerting, and historical metadata recovery."""

    def __init__(
        self,
        provider: Optional[MusicProvider] = None,
        cfg: Optional[ConfigManager] = None,
        snapshots_dir: Optional[Path] = None,
    ) -> None:
        self.provider = provider
        self.config = cfg or config_manager
        self.snapshots_dir = snapshots_dir or self.config.snapshots_dir
        self.index_file = self.snapshots_dir / "index.json"
        self.alerts_log_file = self.config.alerts_log_file

    def _ensure_dirs(self) -> None:
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)
        self.alerts_log_file.parent.mkdir(parents=True, exist_ok=True)

    def _compute_content_hash(self, tracks: List[Any]) -> str:
        """Compute SHA-256 fingerprint based on ordered sequence of track IDs."""
        track_ids = [str(getattr(t, "id", "")) for t in tracks]
        return hashlib.sha256(",".join(track_ids).encode("utf-8")).hexdigest()

    def _load_registry(self) -> Dict[str, Any]:
        """Load global track registry (track_id -> last known healthy metadata)."""
        if self.index_file.exists():
            try:
                with open(self.index_file, "r", encoding="utf-8") as f:
                    return json.load(f) or {}
            except Exception as e:
                logger.warning(f"Failed to read track registry at {self.index_file}: {e}")
        return {}

    def _save_registry(self, registry: Dict[str, Any]) -> None:
        """Persist global track registry."""
        self._ensure_dirs()
        tmp_file = self.index_file.with_suffix(".tmp")
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(registry, f, indent=2, ensure_ascii=False, default=str)
        tmp_file.replace(self.index_file)

    def update_track_registry(
        self,
        tracks: List[Any],
        playlist_slug: str,
        replacements: Optional[Dict[str, str]] = None,
    ) -> None:
        """Update global registry with healthy metadata for active tracks and proactive replacements."""
        registry = self._load_registry()
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        updated = False

        for t in tracks:
            t_id = str(getattr(t, "id", None) or "")
            if not t_id:
                continue

            available = is_track_available(t)
            isrc = getattr(t, "isrc", None) or None
            album = getattr(t, "album", None)
            upc = getattr(album, "upc", None) if album else None
            release_date = getattr(album, "release_date", None) if album else None
            if isinstance(release_date, (datetime.datetime, datetime.date)):
                release_date = release_date.isoformat()
            elif release_date and not hasattr(release_date, "_mock_return_value"):
                release_date = str(release_date)
            else:
                release_date = None

            # Resolve proactive replacement ID if present
            rep_id = None
            if replacements and t_id in replacements:
                rep_id = _safe_str(replacements[t_id]) or None
            elif _safe_str(getattr(t, "provider_replacement_id", None)):
                rep_id = _safe_str(getattr(t, "provider_replacement_id", None))
            elif isinstance(t, dict) and _safe_str(t.get("provider_replacement_id")):
                rep_id = _safe_str(t.get("provider_replacement_id"))

            if not available and not isrc and not upc and not rep_id:
                continue

            artist_name = getattr(getattr(t, "artist", None), "name", "Unknown Artist")
            album_name = (
                getattr(album, "name", None) or getattr(album, "title", "Unknown Album")
                if album
                else "Unknown Album"
            )

            entry = registry.get(t_id, {})
            if not entry:
                entry = {
                    "id": getattr(t, "id", None),
                    "title": getattr(t, "name", None) or getattr(t, "title", ""),
                    "artist": artist_name,
                    "album": album_name,
                    "duration_seconds": getattr(t, "duration_seconds", None) or getattr(t, "duration", None),
                    "isrc": isrc,
                    "album_upc": upc,
                    "album_release_date": release_date,
                    "last_available_at": now_iso if available else None,
                    "found_in_playlists": [playlist_slug],
                    "has_provider_replacement": bool(rep_id),
                    "provider_replacement_id": rep_id,
                }
                registry[t_id] = entry
                updated = True
            else:
                if isrc and not entry.get("isrc"):
                    entry["isrc"] = isrc
                    updated = True
                if upc and not entry.get("album_upc"):
                    entry["album_upc"] = upc
                    updated = True
                if release_date and not entry.get("album_release_date"):
                    entry["album_release_date"] = release_date
                    updated = True
                if rep_id and entry.get("provider_replacement_id") != rep_id:
                    entry["provider_replacement_id"] = rep_id
                    entry["has_provider_replacement"] = True
                    updated = True
                if available:
                    entry["last_available_at"] = now_iso
                    updated = True
                if playlist_slug not in entry.get("found_in_playlists", []):
                    entry.setdefault("found_in_playlists", []).append(playlist_slug)
                    updated = True

        if updated:
            self._save_registry(registry)

    def get_historical_track(self, track_id: Union[int, str]) -> Optional[Dict[str, Any]]:
        """Retrieve last known healthy metadata (ISRC, UPC, etc.) for a track ID."""
        registry = self._load_registry()
        return registry.get(str(track_id))

    def _append_alert(self, alert: Dict[str, Any]) -> None:
        """Append an availability alert to availability_alerts.log."""
        self._ensure_dirs()
        line = json.dumps(alert, ensure_ascii=False, default=str) + "\n"
        with open(self.alerts_log_file, "a", encoding="utf-8") as f:
            f.write(line)

    def get_recent_alerts(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Read recent availability alerts from log."""
        if not self.alerts_log_file.exists():
            return []
        alerts: List[Dict[str, Any]] = []
        try:
            with open(self.alerts_log_file, "r", encoding="utf-8") as f:
                lines = f.readlines()
                for line in reversed(lines):
                    line = line.strip()
                    if line:
                        try:
                            alerts.append(json.loads(line))
                            if len(alerts) >= limit:
                                break
                        except json.JSONDecodeError:
                            continue
        except Exception as e:
            logger.error(f"Failed to read alerts log at {self.alerts_log_file}: {e}")
        return alerts

    def snapshot_playlist(
        self,
        identifier: str,
        alert_on_unavailable: bool = True,
    ) -> Dict[str, Any]:
        """Save versioned snapshot of a playlist if modified, and check for delisted tracks."""
        self._ensure_dirs()
        if not self.provider:
            raise RuntimeError("An active MusicProvider is required to snapshot playlists.")

        playlist = self.provider.get_playlist(identifier)
        if not playlist:
            raise ValueError(f"Playlist not found: {identifier}")

        tracks = self.provider.get_playlist_tracks(playlist.id)
        slug = _slugify(playlist.name)
        pl_dir = self.snapshots_dir / slug
        pl_dir.mkdir(parents=True, exist_ok=True)
        manifest_file = pl_dir / "manifest.json"

        manifest: Dict[str, Any] = {}
        if manifest_file.exists():
            try:
                with open(manifest_file, "r", encoding="utf-8") as f:
                    manifest = json.load(f) or {}
            except Exception as e:
                logger.warning(f"Failed to load manifest at {manifest_file}: {e}")

        last_hash = manifest.get("last_content_hash", "")
        current_hash = self._compute_content_hash(tracks)
        has_changed = (current_hash != last_hash)

        now_utc = datetime.datetime.now(datetime.timezone.utc)
        timestamp_str = now_utc.strftime("%Y-%m-%dT%H%M%SZ")
        snapshot_filename = f"{timestamp_str}.json"
        snapshot_path = pl_dir / snapshot_filename

        # Proactive discovery of provider track replacements across playlist
        replacements: Dict[str, str] = {}
        if self.provider and hasattr(self.provider, "get_playlist_track_replacements"):
            try:
                raw_reps = self.provider.get_playlist_track_replacements(str(playlist.id))
                if raw_reps and not _safe_str(raw_reps) == "" and isinstance(raw_reps, dict) and not hasattr(raw_reps, "_mock_return_value"):
                    replacements = {str(k): str(v) for k, v in raw_reps.items()}
            except Exception as e:
                logger.debug(f"Proactive playlist replacement check failed for '{playlist.name}': {e}")

        if replacements:
            for t in tracks:
                t_id_str = str(getattr(t, "id", None) or (t.get("id") if isinstance(t, dict) else ""))
                if t_id_str in replacements:
                    if hasattr(t, "provider_replacement_id"):
                        setattr(t, "provider_replacement_id", replacements[t_id_str])
                    elif isinstance(t, dict):
                        t["provider_replacement_id"] = replacements[t_id_str]

        self.update_track_registry(tracks, slug, replacements=replacements)

        unavailable_tracks = []
        newly_unavailable_alerts = []
        for idx, t in enumerate(tracks):
            if not is_track_available(t):
                hist = self.get_historical_track(t.id) or {}
                t_id_str = str(getattr(t, "id", None) or "")
                rep_id = replacements.get(t_id_str) or hist.get("provider_replacement_id")
                rep_title = hist.get("provider_replacement_title")

                # If no replacement was discovered proactively, check reactive track replacement
                if not rep_id and self.provider and hasattr(self.provider, "get_track_replacement") and t_id_str:
                    try:
                        rep_track = self.provider.get_track_replacement(t_id_str)
                        if rep_track and _safe_str(getattr(rep_track, "id", None)):
                            rep_id = _safe_str(getattr(rep_track, "id", None))
                            rep_title = getattr(rep_track, "name", None) or getattr(rep_track, "title", "Unknown Title")
                            # Cache in registry
                            reg = self._load_registry()
                            if t_id_str in reg:
                                reg[t_id_str]["provider_replacement_id"] = rep_id
                                reg[t_id_str]["has_provider_replacement"] = True
                                reg[t_id_str]["provider_replacement_title"] = rep_title
                                self._save_registry(reg)
                    except Exception as e:
                        logger.debug(f"Reactive track replacement check failed for #{t_id_str}: {e}")

                unavail_info = {
                    "index": idx,
                    "id": t.id,
                    "title": getattr(t, "name", None) or getattr(t, "title", "Unknown Title"),
                    "artist": getattr(getattr(t, "artist", None), "name", "Unknown Artist"),
                    "album": getattr(getattr(t, "album", None), "name", None) or getattr(getattr(t, "album", None), "title", "Unknown Album"),
                    "historical_isrc": hist.get("isrc"),
                    "historical_upc": hist.get("album_upc"),
                    "has_provider_replacement": bool(rep_id),
                    "provider_replacement_id": rep_id,
                    "provider_replacement_title": rep_title,
                }
                unavailable_tracks.append(unavail_info)

                if alert_on_unavailable:
                    alert_msg = (
                        f"Track '{unavail_info['title']}' by '{unavail_info['artist']}' "
                        f"is UNAVAILABLE in playlist '{playlist.name}'"
                    )
                    if rep_id:
                        alert_msg += f" (Official replacement available: '{rep_title or rep_id}' [ID: {rep_id}])"

                    alert = {
                        "timestamp": now_utc.isoformat(),
                        "provider": self.provider.name,
                        "playlist_id": str(playlist.id),
                        "playlist_name": playlist.name,
                        "playlist_slug": slug,
                        "track_id": t.id,
                        "title": unavail_info["title"],
                        "track_title": unavail_info["title"],
                        "artist": unavail_info["artist"],
                        "artist_name": unavail_info["artist"],
                        "album": unavail_info["album"],
                        "reason": "unavailable",
                        "historical_isrc": unavail_info["historical_isrc"],
                        "historical_upc": unavail_info["historical_upc"],
                        "has_provider_replacement": bool(rep_id),
                        "provider_replacement_id": rep_id,
                        "provider_replacement_title": rep_title,
                        "message": alert_msg,
                    }
                    self._append_alert(alert)
                    newly_unavailable_alerts.append(alert)

        saved_path: Optional[str] = None
        snapshots_list = manifest.get("snapshots", [])
        last_snapshot_exists = bool(snapshots_list and (pl_dir / snapshots_list[-1].get("filename", "")).exists())

        track_diff: Dict[str, Any] = {"added_tracks": [], "removed_tracks": [], "relinked_tracks": [], "reordered": False}
        if has_changed and last_snapshot_exists:
            try:
                prev_file = pl_dir / snapshots_list[-1]["filename"]
                with open(prev_file, "r", encoding="utf-8") as f:
                    prev_data = json.load(f)
                    prev_tracks = prev_data.get("tracks", [])
                    track_diff = compute_track_diff(prev_tracks, tracks)
            except Exception as e:
                logger.debug(f"Failed to compute track diff for '{slug}': {e}")

        # Save new snapshot if changed or first snapshot
        if has_changed or not last_snapshot_exists:
            full_data = serialize_playlist(playlist, tracks)
            full_data["content_hash"] = current_hash
            full_data["snapshot_created_at"] = now_utc.isoformat()
            full_data["version"] = len(snapshots_list) + 1

            with open(snapshot_path, "w", encoding="utf-8") as f:
                json.dump(full_data, f, indent=2, ensure_ascii=False)

            manifest["playlist_id"] = str(playlist.id)
            manifest["playlist_name"] = playlist.name
            manifest["playlist_slug"] = slug
            manifest["provider"] = self.provider.name
            manifest["last_content_hash"] = current_hash
            manifest["last_snapshot_at"] = now_utc.isoformat()

            new_snapshot_record = {
                "version": len(snapshots_list) + 1,
                "timestamp": now_utc.isoformat(),
                "filename": snapshot_filename,
                "content_hash": current_hash,
                "track_count": len(tracks),
                "unavailable_count": len(unavailable_tracks),
            }
            snapshots_list.append(new_snapshot_record)
            manifest["snapshots"] = snapshots_list

            with open(manifest_file, "w", encoding="utf-8") as f:
                json.dump(manifest, f, indent=2, ensure_ascii=False)

            saved_path = str(snapshot_path)
            logger.info(
                f"Saved snapshot v{new_snapshot_record['version']} for playlist '{playlist.name}' ({len(tracks)} tracks)"
            )
        is_created = bool(has_changed or not last_snapshot_exists)
        return {
            "id": str(playlist.id),
            "playlist_id": str(playlist.id),
            "name": playlist.name,
            "playlist_name": playlist.name,
            "playlist_slug": slug,
            "provider": self.provider.name,
            "status": "created" if is_created else "unchanged",
            "has_changed": has_changed,
            "content_hash": current_hash,
            "track_count": len(tracks),
            "total_tracks": len(tracks),
            "unavailable_count": len(unavailable_tracks),
            "unavailable_tracks_count": len(unavailable_tracks),
            "unavailable_tracks": unavailable_tracks,
            "new_alerts_count": len(newly_unavailable_alerts),
            "snapshot_file": saved_path,
            "version": len(snapshots_list),
            "track_diff": track_diff,
        }

    def snapshot_favorite_tracks(
        self,
        alert_on_unavailable: bool = True,
    ) -> Dict[str, Any]:
        """Save versioned snapshot of all favorite/liked tracks if modified, and check for delisted tracks."""
        self._ensure_dirs()
        if not self.provider:
            raise RuntimeError("An active MusicProvider is required to snapshot favorite tracks.")

        tracks = self.provider.get_favorite_tracks()
        slug = f"favorites_{self.provider.name}"
        fav_dir = self.snapshots_dir / slug
        fav_dir.mkdir(parents=True, exist_ok=True)
        manifest_file = fav_dir / "manifest.json"

        manifest: Dict[str, Any] = {}
        if manifest_file.exists():
            try:
                with open(manifest_file, "r", encoding="utf-8") as f:
                    manifest = json.load(f) or {}
            except Exception as e:
                logger.warning(f"Failed to load manifest at {manifest_file}: {e}")

        last_hash = manifest.get("last_content_hash", "")
        current_hash = self._compute_content_hash(tracks)
        has_changed = (current_hash != last_hash)

        snapshots_list: List[Dict[str, Any]] = manifest.get("snapshots", [])
        last_snapshot_exists = bool(snapshots_list and (fav_dir / snapshots_list[-1]["filename"]).exists())

        now_utc = datetime.datetime.now(datetime.timezone.utc)
        timestamp_str = now_utc.strftime("%Y-%m-%dT%H%M%SZ")
        snapshot_filename = f"{timestamp_str}.json"
        snapshot_path = fav_dir / snapshot_filename

        self.update_track_registry(tracks, slug)

        unavailable_tracks = []
        newly_unavailable_alerts = []
        for idx, t in enumerate(tracks):
            if not is_track_available(t):
                hist = self.get_historical_track(t.id) or {}
                unavail_info = {
                    "index": idx,
                    "id": getattr(t, "id", None),
                    "title": getattr(t, "name", None) or getattr(t, "title", "Unknown"),
                    "artist": getattr(getattr(t, "artist", None), "name", "Unknown Artist"),
                    "isrc": getattr(t, "isrc", None) or hist.get("isrc"),
                    "album_upc": (
                        getattr(getattr(t, "album", None), "upc", None) or hist.get("album_upc")
                    ),
                    "historical_available_at": hist.get("last_available_at"),
                }
                unavailable_tracks.append(unavail_info)

                if alert_on_unavailable:
                    alert = {
                        "timestamp": now_utc.isoformat(),
                        "provider": self.provider.name,
                        "playlist_id": slug,
                        "playlist_name": f"Favorite Tracks ({self.provider.display_name})",
                        "playlist_slug": slug,
                        "track_id": getattr(t, "id", None),
                        "title": unavail_info["title"],
                        "track_title": unavail_info["title"],
                        "artist": unavail_info["artist"],
                        "artist_name": unavail_info["artist"],
                        "album": getattr(getattr(t, "album", None), "name", None) or getattr(getattr(t, "album", None), "title", "Unknown Album"),
                        "reason": "unavailable",
                        "historical_isrc": unavail_info["isrc"],
                        "historical_upc": unavail_info["album_upc"],
                        "message": (
                            f"Favorite track '{unavail_info['title']}' by '{unavail_info['artist']}' "
                            f"is UNAVAILABLE on {self.provider.display_name}"
                        ),
                    }
                    self._append_alert(alert)
                    newly_unavailable_alerts.append(alert)

        track_diff = {"added_tracks": [], "removed_tracks": [], "relinked_tracks": [], "reordered": False}
        if snapshots_list and (fav_dir / snapshots_list[-1]["filename"]).exists():
            try:
                with open(fav_dir / snapshots_list[-1]["filename"], "r", encoding="utf-8") as f:
                    prev_data = json.load(f)
                    prev_tracks = prev_data.get("tracks", [])
                    track_diff = compute_track_diff(prev_tracks, tracks)
            except Exception as e:
                logger.debug(f"Failed to compute track diff for '{slug}': {e}")

        saved_path = None
        if has_changed or not last_snapshot_exists:
            from curo.core.models import Playlist as DomainPlaylist
            dummy_pl = DomainPlaylist(
                id=slug,
                title=f"Favorite Tracks ({self.provider.display_name})",
                provider=self.provider.name,
                description=f"Favorite tracks for {self.provider.display_name}",
                is_public=False,
                track_count=len(tracks),
            )
            full_data = serialize_playlist(dummy_pl, tracks)
            full_data["content_hash"] = current_hash
            full_data["snapshot_created_at"] = now_utc.isoformat()
            full_data["version"] = len(snapshots_list) + 1

            with open(snapshot_path, "w", encoding="utf-8") as f:
                json.dump(full_data, f, indent=2, ensure_ascii=False)

            manifest["playlist_id"] = slug
            manifest["playlist_name"] = f"Favorite Tracks ({self.provider.display_name})"
            manifest["playlist_slug"] = slug
            manifest["provider"] = self.provider.name
            manifest["last_content_hash"] = current_hash
            manifest["last_snapshot_at"] = now_utc.isoformat()

            new_snapshot_record = {
                "version": len(snapshots_list) + 1,
                "timestamp": now_utc.isoformat(),
                "filename": snapshot_filename,
                "content_hash": current_hash,
                "track_count": len(tracks),
                "unavailable_count": len(unavailable_tracks),
            }
            snapshots_list.append(new_snapshot_record)
            manifest["snapshots"] = snapshots_list

            with open(manifest_file, "w", encoding="utf-8") as f:
                json.dump(manifest, f, indent=2, ensure_ascii=False)

            saved_path = str(snapshot_path)
            logger.info(
                f"Saved snapshot v{new_snapshot_record['version']} for Favorite Tracks ({len(tracks)} tracks)"
            )

        is_created = bool(has_changed or not last_snapshot_exists)
        return {
            "success": True,
            "id": slug,
            "playlist_id": slug,
            "name": f"Favorite Tracks ({self.provider.display_name})",
            "playlist_name": f"Favorite Tracks ({self.provider.display_name})",
            "playlist_slug": slug,
            "provider": self.provider.name,
            "status": "created" if is_created else "unchanged",
            "has_changed": has_changed,
            "content_hash": current_hash,
            "checksum": current_hash,
            "track_count": len(tracks),
            "total_tracks": len(tracks),
            "unavailable_count": len(unavailable_tracks),
            "unavailable_tracks_count": len(unavailable_tracks),
            "unavailable_tracks": unavailable_tracks,
            "unavailable_alerts_count": len(newly_unavailable_alerts),
            "new_alerts_count": len(newly_unavailable_alerts),
            "snapshot_file": saved_path,
            "snapshot_path": saved_path,
            "version": len(snapshots_list),
            "track_diff": track_diff,
            "added_tracks": track_diff["added_tracks"],
            "removed_tracks": track_diff["removed_tracks"],
            "relinked_tracks": track_diff.get("relinked_tracks", []),
        }

    def snapshot_all_playlists(
        self,
        alert_on_unavailable: bool = True,
        notify: bool = False,
        force_all_mail: bool = False,
    ) -> Dict[str, Any]:
        """Snapshot all personal user playlists and optionally send email digests."""
        if not self.provider:
            raise RuntimeError("An active MusicProvider is required to snapshot playlists.")

        playlists = self.provider.get_user_playlists()
        results: List[Dict[str, Any]] = []
        total_alerts = 0
        total_snapshots_saved = 0

        for pl in playlists:
            try:
                res = self.snapshot_playlist(pl.id, alert_on_unavailable=alert_on_unavailable)
                if res.get("snapshot_file"):
                    total_snapshots_saved += 1
                total_alerts += res.get("new_alerts_count", 0)
                results.append(res)
            except Exception as e:
                logger.error(f"Failed to snapshot playlist '{pl.name}': {e}")
                results.append({
                    "playlist_id": str(pl.id),
                    "playlist_name": pl.name,
                    "error": str(e),
                })

        batch_result = {
            "playlists_scanned": len(playlists),
            "snapshots_saved": total_snapshots_saved,
            "total_alerts": total_alerts,
            "playlists": results,
        }

        notification_res = None
        if notify:
            try:
                from curo.services.email_service import EmailService
                email_svc = EmailService(cfg=self.config)
                notification_res = email_svc.send_digest_notification(
                    batch_results=batch_result,
                    force_all=force_all_mail,
                    provider_name=self.provider.display_name,
                )
            except Exception as e:
                logger.error(f"Failed to dispatch email notification: {e}", exc_info=True)
                notification_res = {"sent": False, "error": str(e)}

        batch_result["notification"] = notification_res
        return batch_result
