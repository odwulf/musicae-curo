"""Playlist repair and track replacement service."""

from __future__ import annotations

import datetime
import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from curo.config import ConfigManager, config_manager
from curo.core.models import Track
from curo.core.provider import MusicProvider
from curo.services.candidate_finder import CandidateFinder
from curo.services.playlist_service import PlaylistService
from curo.services.similarity_scorer import (
    CandidateEvaluation,
    _clean_str,
    _is_mock,
    evaluate_candidate,
)
from curo.services.track_matcher import is_track_available

logger = logging.getLogger(__name__)


def _slugify(text: str) -> str:
    """Create a filename-safe slug from playlist name."""
    s = re.sub(r"[^\w\s-]", "", text).strip().lower()
    return re.sub(r"[-\s]+", "_", s) or "playlist"


class PlaylistRepairService:
    """Inspects playlists for retired/unavailable tracks, finds replacements, and generates reports."""

    def __init__(
        self,
        provider: MusicProvider,
        project_root: Optional[Path] = None,
        cfg: Optional[ConfigManager] = None,
    ) -> None:
        self.provider = provider
        self.config = cfg or config_manager
        self.playlist_service = PlaylistService(provider)
        from curo.services.snapshot_service import SnapshotService
        self.snapshot_service = SnapshotService(provider, cfg=self.config)
        self.candidate_finder = CandidateFinder(provider, snapshot_service=self.snapshot_service)
        self.project_root = project_root or Path.cwd()

    def analyze_and_repair(
        self,
        identifier: str,
        threshold: float = 0.8,
        dry_run: bool = True,
        all_tracks: bool = False,
        export_path: Optional[Path] = None,
        target_track: Optional[Union[str, int]] = None,
        replacement: Optional[Union[str, int]] = None,
        isrc: Optional[str] = None,
        upc: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Scan playlist for unavailable tracks, score candidates, and optionally apply replacements."""
        playlist = self.playlist_service.get_playlist(identifier)
        if not playlist:
            raise ValueError(f"Playlist not found: {identifier}")

        pl_id = str(getattr(playlist, "id", identifier))
        pl_name = getattr(playlist, "title", None) or getattr(playlist, "name", "Playlist")
        tracks = self.provider.get_playlist_tracks(pl_id)
        unavailable_reports: List[Dict[str, Any]] = []
        replacements_applied = 0

        logger.info(f"Analyzing {len(tracks)} tracks in playlist '{pl_name}'...")

        for idx, t in enumerate(tracks):
            available = is_track_available(t)

            # If user targeted a specific track ID or index, filter for it
            if target_track is not None:
                t_str = str(target_track).strip()
                clean_target = t_str.lstrip("#")
                is_match = False
                if str(getattr(t, "id", "")) == t_str:
                    is_match = True
                elif clean_target.isdigit():
                    val = int(clean_target)
                    if val == idx + 1 or val == idx:
                        is_match = True
                if not is_match:
                    continue
            elif available and not all_tracks:
                continue

            orig_artist = _clean_str(getattr(getattr(t, "artist", None), "name", None)) or "Unknown Artist"
            orig_album = (
                _clean_str(getattr(getattr(t, "album", None), "name", None))
                or _clean_str(getattr(getattr(t, "album", None), "title", None))
                or "Unknown Album"
            )
            orig_upc = _clean_str(getattr(getattr(t, "album", None), "upc", None))
            raw_orig_date = getattr(getattr(t, "album", None), "release_date", None)
            orig_release_date = (
                raw_orig_date.isoformat()
                if isinstance(raw_orig_date, (datetime.datetime, datetime.date))
                else (str(raw_orig_date) if raw_orig_date and not _is_mock(raw_orig_date) else None)
            )

            hist = None
            if self.snapshot_service and getattr(t, "id", None) is not None:
                try:
                    hist = self.snapshot_service.get_historical_track(t.id)
                except Exception:
                    hist = None

            orig_isrc = _clean_str(getattr(t, "isrc", None)) or (hist.get("isrc") if hist else None)
            recovered_isrc = bool(not getattr(t, "isrc", None) and hist and hist.get("isrc"))
            recovered_upc = bool(not orig_upc and hist and hist.get("album_upc"))
            if recovered_upc:
                orig_upc = hist.get("album_upc")
            if not orig_release_date and hist and hist.get("album_release_date"):
                orig_release_date = hist.get("album_release_date")

            # Find candidates
            if (replacement or isrc or upc) and target_track is not None:
                try:
                    cand_track, method, eval_result = self.resolve_replacement_track(
                        original_track=t,
                        replacement=replacement,
                        isrc=isrc,
                        upc=upc,
                    )
                    candidates = [eval_result]
                except Exception as e:
                    logger.error(f"Failed to resolve candidate for track #{idx} '{getattr(t, 'name', '')}': {e}")
                    candidates = []
            else:
                candidates = self.candidate_finder.find_candidates(t, max_candidates=10)

            cand_dicts = []
            best_candidate_dict = None

            for c in candidates:
                d = c.to_dict()
                d["meets_threshold"] = bool(c.score >= threshold and not c.disqualified)
                cand_dicts.append(d)

            if cand_dicts:
                best_candidate_dict = cand_dicts[0]

            t_title = _clean_str(getattr(t, "name", None)) or _clean_str(getattr(t, "title", "")) or ""
            t_dur = getattr(t, "duration_seconds", None) or getattr(t, "duration", None)
            if _is_mock(t_dur):
                t_dur = None

            track_entry = {
                "index_in_playlist": idx,
                "original_track": {
                    "id": getattr(t, "id", None) if not _is_mock(getattr(t, "id", None)) else None,
                    "title": t_title,
                    "artist": orig_artist,
                    "album": orig_album,
                    "album_release_date": orig_release_date,
                    "duration_seconds": t_dur,
                    "isrc": orig_isrc,
                    "recovered_isrc": recovered_isrc,
                    "album_upc": orig_upc,
                    "recovered_album_upc": recovered_upc,
                    "available": available,
                },
                "candidate_count": len(candidates),
                "best_candidate": best_candidate_dict,
                "all_candidates": cand_dicts,
                "replacement_status": "pending",
            }

            can_replace = False
            if best_candidate_dict and best_candidate_dict["meets_threshold"]:
                if best_candidate_dict["requires_manual_approval"]:
                    track_entry["replacement_status"] = "skipped_manual_approval_required"
                else:
                    can_replace = True

            if can_replace:
                if not dry_run:
                    try:
                        new_track_id = str(best_candidate_dict["id"])
                        added = self.provider.add_tracks_to_playlist(pl_id, [new_track_id], position=idx)
                        if not added:
                            logger.error(
                                f"Provider did not add candidate ID {new_track_id} for track #{idx} '{t_title}'"
                            )
                            track_entry["replacement_status"] = "error: provider_skipped_add"
                        else:
                            removed = self.provider.remove_track_from_playlist_by_index(pl_id, idx + 1)
                            if not removed:
                                logger.error(f"Failed to remove old track at index {idx+1}")
                                track_entry["replacement_status"] = "error: failed_to_remove_old_track"
                            else:
                                track_entry["replacement_status"] = "replaced"
                                replacements_applied += 1
                                logger.info(
                                    f"Replaced track #{idx} '{t_title}' with candidate ID {new_track_id} (Score: {best_candidate_dict['similarity_score']})"
                                )
                    except Exception as e:
                        logger.error(f"Failed to replace track #{idx}: {e}")
                        track_entry["replacement_status"] = f"error: {e}"
                else:
                    track_entry["replacement_status"] = "would_replace" if not dry_run else "dry_run_eligible"
            elif not best_candidate_dict or not best_candidate_dict["meets_threshold"]:
                track_entry["replacement_status"] = "no_candidate_meets_threshold"

            unavailable_reports.append(track_entry)

        timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
        report = {
            "report_generated_at": timestamp,
            "provider": self.provider.name,
            "playlist_id": pl_id,
            "playlist_name": pl_name,
            "total_tracks": len(tracks),
            "unavailable_tracks_count": len(unavailable_reports),
            "threshold_configured": threshold,
            "dry_run": dry_run,
            "replacements_applied": replacements_applied,
            "unavailable_tracks": unavailable_reports,
        }

        if export_path:
            out_file = Path(export_path)
        else:
            reports_dir = self.project_root / "reports"
            reports_dir.mkdir(parents=True, exist_ok=True)
            slug = _slugify(pl_name)
            date_tag = datetime.date.today().isoformat()
            out_file = reports_dir / f"repair_{slug}_{date_tag}.json"

        out_file.parent.mkdir(parents=True, exist_ok=True)
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)

        report["exported_to"] = str(out_file)
        return report

    def resolve_replacement_track(
        self,
        original_track: Any,
        replacement: Optional[Union[str, int]] = None,
        isrc: Optional[str] = None,
        upc: Optional[str] = None,
    ) -> tuple[Any, str, CandidateEvaluation]:
        """Resolve a candidate replacement Track object from a track ID, ISRC, or UPC."""
        # 1. Check if explicit ISRC is provided or if replacement matches ISRC format
        isrc_to_search = isrc.strip() if isrc else None
        if not isrc_to_search and replacement:
            rep_clean = str(replacement).strip()
            if re.match(r"^[A-Za-z]{2}[A-Za-z0-9]{3}\d{7}$", rep_clean):
                isrc_to_search = rep_clean.upper()

        if isrc_to_search:
            method = f"isrc ({isrc_to_search})"
            try:
                tracks = self.provider.get_tracks_by_isrc(isrc_to_search)
                available = [t for t in (tracks or []) if is_track_available(t)]
                evals = [
                    evaluate_candidate(original_track, t, original_isrc=isrc_to_search)
                    for t in (available or tracks or [])
                ]
                if evals:
                    evals.sort(key=lambda c: (-c.score, c.disqualified))
                    return evals[0].candidate, method, evals[0]
            except Exception as e:
                logger.warning(f"Error querying tracks by ISRC '{isrc_to_search}': {e}")

        # 2. Check if explicit UPC is provided or if replacement matches barcode format
        upc_to_search = upc.strip() if upc else None
        if not upc_to_search and replacement and not isrc_to_search:
            rep_clean = str(replacement).strip()
            if rep_clean.isdigit() and len(rep_clean) in (12, 13):
                upc_to_search = rep_clean

        if upc_to_search:
            method = f"upc ({upc_to_search})"
            try:
                albums = self.provider.get_albums_by_barcode(upc_to_search)
                evals = []
                for alb in albums or []:
                    alb_upc = getattr(alb, "upc", upc_to_search)
                    alb_id = getattr(alb, "id", None)
                    tracks = (
                        self.provider.get_album_tracks(str(alb_id))
                        if alb_id
                        else (alb.tracks() if hasattr(alb, "tracks") else [])
                    )
                    for t in tracks or []:
                        if is_track_available(t):
                            evals.append(
                                evaluate_candidate(
                                    original_track,
                                    t,
                                    original_upc=upc_to_search,
                                    candidate_upc=alb_upc,
                                )
                            )
                if evals:
                    evals.sort(key=lambda c: (-c.score, c.disqualified))
                    return evals[0].candidate, method, evals[0]
            except Exception as e:
                logger.warning(f"Error querying album by barcode '{upc_to_search}': {e}")

        # 3. Direct Track ID
        track_id_to_fetch = None
        if replacement and not isrc_to_search and not upc_to_search:
            rep_clean = str(replacement).strip()
            if rep_clean.isdigit():
                track_id_to_fetch = rep_clean

        if track_id_to_fetch is not None:
            method = f"track_id ({track_id_to_fetch})"
            try:
                cand_track = self.provider.get_track(track_id_to_fetch)
                if cand_track:
                    eval_res = evaluate_candidate(original_track, cand_track)
                    return cand_track, method, eval_res
            except Exception as e:
                raise ValueError(f"Failed to fetch replacement track ID {track_id_to_fetch}: {e}")

        raise ValueError(
            f"Could not find or resolve any replacement track for "
            f"replacement='{replacement}', isrc='{isrc}', upc='{upc}'"
        )

    def replace_track(
        self,
        playlist_identifier: str,
        target: Union[str, int],
        replacement: Optional[Union[str, int]] = None,
        isrc: Optional[str] = None,
        upc: Optional[str] = None,
        dry_run: bool = True,
    ) -> Dict[str, Any]:
        """Replace a specific track in a playlist by track ID, playlist position, ISRC, or UPC."""
        playlist = self.playlist_service.get_playlist(playlist_identifier)
        if not playlist:
            raise ValueError(f"Playlist not found: {playlist_identifier}")

        pl_id = str(getattr(playlist, "id", playlist_identifier))
        pl_name = getattr(playlist, "title", None) or getattr(playlist, "name", "Playlist")
        tracks = self.provider.get_playlist_tracks(pl_id)
        target_str = str(target).strip()

        target_idx = None
        target_track = None

        for i, t in enumerate(tracks):
            if str(getattr(t, "id", "")) == target_str:
                target_idx = i
                target_track = t
                break

        if target_idx is None:
            clean_idx_str = target_str.lstrip("#")
            if clean_idx_str.isdigit():
                val = int(clean_idx_str)
                if 1 <= val <= len(tracks):
                    target_idx = val - 1
                    target_track = tracks[target_idx]
                elif val == 0 and len(tracks) > 0:
                    target_idx = 0
                    target_track = tracks[0]

        if target_track is None or target_idx is None:
            raise ValueError(
                f"Track '{target}' not found in playlist '{pl_name}' (contains {len(tracks)} tracks)."
            )

        cand_track, method, eval_result = self.resolve_replacement_track(
            original_track=target_track,
            replacement=replacement,
            isrc=isrc,
            upc=upc,
        )

        orig_artist = _clean_str(getattr(getattr(target_track, "artist", None), "name", None)) or "Unknown Artist"
        orig_album = (
            _clean_str(getattr(getattr(target_track, "album", None), "name", None))
            or _clean_str(getattr(getattr(target_track, "album", None), "title", None))
            or "Unknown Album"
        )
        orig_isrc = _clean_str(getattr(target_track, "isrc", None))
        orig_upc = _clean_str(getattr(getattr(target_track, "album", None), "upc", None))
        orig_dur = getattr(target_track, "duration_seconds", None) or getattr(target_track, "duration", None)
        if _is_mock(orig_dur):
            orig_dur = None

        cand_artist = _clean_str(getattr(getattr(cand_track, "artist", None), "name", None)) or "Unknown Artist"
        cand_album = (
            _clean_str(getattr(getattr(cand_track, "album", None), "name", None))
            or _clean_str(getattr(getattr(cand_track, "album", None), "title", None))
            or "Unknown Album"
        )
        cand_isrc = _clean_str(getattr(cand_track, "isrc", None))
        cand_upc = _clean_str(getattr(getattr(cand_track, "album", None), "upc", None))
        cand_dur = getattr(cand_track, "duration_seconds", None) or getattr(cand_track, "duration", None)
        if _is_mock(cand_dur):
            cand_dur = None

        res_dict = {
            "provider": self.provider.name,
            "playlist_id": pl_id,
            "playlist_name": pl_name,
            "index_in_playlist": target_idx,
            "position": target_idx + 1,
            "resolution_method": method,
            "dry_run": dry_run,
            "original_track": {
                "id": getattr(target_track, "id", None) if not _is_mock(getattr(target_track, "id", None)) else None,
                "title": _clean_str(getattr(target_track, "name", None)) or _clean_str(getattr(target_track, "title", None)) or "",
                "artist": orig_artist,
                "album": orig_album,
                "duration_seconds": orig_dur,
                "isrc": orig_isrc,
                "album_upc": orig_upc,
                "available": is_track_available(target_track),
            },
            "replacement_track": {
                "id": getattr(cand_track, "id", None) if not _is_mock(getattr(cand_track, "id", None)) else None,
                "title": _clean_str(getattr(cand_track, "name", None)) or _clean_str(getattr(cand_track, "title", None)) or "",
                "artist": cand_artist,
                "album": cand_album,
                "duration_seconds": cand_dur,
                "isrc": cand_isrc,
                "album_upc": cand_upc,
                "available": is_track_available(cand_track),
            },
            "similarity_score": round(eval_result.score, 2),
            "reasoning": eval_result.reasoning,
            "status": "simulated" if dry_run else "pending",
        }

        if not dry_run:
            try:
                new_id = str(cand_track.id)
                added = self.provider.add_tracks_to_playlist(pl_id, [new_id], position=target_idx)
                if not added:
                    res_dict["status"] = "error: provider_skipped_add"
                else:
                    removed = self.provider.remove_track_from_playlist_by_index(pl_id, target_idx + 1)
                    if not removed:
                        res_dict["status"] = "error: failed_to_remove_old_track"
                    else:
                        res_dict["status"] = "replaced"
                        slug = _slugify(pl_name)
                        self.snapshot_service.update_track_registry([cand_track], slug)
            except Exception as e:
                res_dict["status"] = f"error: {e}"

        return res_dict

