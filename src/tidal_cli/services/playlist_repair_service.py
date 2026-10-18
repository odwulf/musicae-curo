"""Playlist repair and track replacement service."""

from __future__ import annotations

import datetime
import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import tidalapi
from tidalapi.playlist import UserPlaylist

from tidal_cli.services.candidate_finder import CandidateFinder
from tidal_cli.services.playlist_service import PlaylistService
from tidal_cli.services.track_matcher import is_track_available

logger = logging.getLogger(__name__)


def _slugify(text: str) -> str:
    """Create a filename-safe slug from playlist name."""
    s = re.sub(r"[^\w\s-]", "", text).strip().lower()
    return re.sub(r"[-\s]+", "_", s) or "playlist"


class PlaylistRepairService:
    """Inspects playlists for retired/unavailable tracks, finds replacements, and generates reports."""

    def __init__(self, session: tidalapi.Session, project_root: Optional[Path] = None) -> None:
        self.session = session
        self.playlist_service = PlaylistService(session)
        self.candidate_finder = CandidateFinder(session)
        self.project_root = project_root or Path.cwd()

    def analyze_and_repair(
        self,
        identifier: str,
        threshold: float = 0.8,
        dry_run: bool = True,
        all_tracks: bool = False,
        export_path: Optional[Path] = None,
    ) -> Dict[str, Any]:
        """Scan playlist for unavailable tracks, score candidates, and optionally apply replacements."""
        playlist = self.playlist_service.get_playlist(identifier)
        if not playlist:
            raise ValueError(f"Playlist not found: {identifier}")

        tracks = playlist.tracks()
        unavailable_reports: List[Dict[str, Any]] = []
        replacements_applied = 0

        logger.info(f"Analyzing {len(tracks)} tracks in playlist '{playlist.name}'...")

        for idx, t in enumerate(tracks):
            available = is_track_available(t)

            # If track is available and we're not checking all tracks, skip
            if available and not all_tracks:
                continue

            orig_artist = getattr(getattr(t, "artist", None), "name", "Unknown Artist")
            orig_album = getattr(getattr(t, "album", None), "name", "Unknown Album")
            orig_upc = getattr(getattr(t, "album", None), "upc", None)
            raw_orig_date = getattr(getattr(t, "album", None), "release_date", None)
            orig_release_date = (
                raw_orig_date.isoformat()
                if isinstance(raw_orig_date, (datetime.datetime, datetime.date))
                else (str(raw_orig_date) if raw_orig_date and not hasattr(raw_orig_date, "_mock_return_value") else None)
            )

            # Find candidates
            candidates = self.candidate_finder.find_candidates(t, max_candidates=10)

            # Mark candidates against threshold
            cand_dicts = []
            best_candidate_dict = None

            for c in candidates:
                d = c.to_dict()
                d["meets_threshold"] = bool(c.score >= threshold and not c.disqualified)
                cand_dicts.append(d)

            if cand_dicts:
                best_candidate_dict = cand_dicts[0]

            track_entry = {
                "index_in_playlist": idx,
                "original_track": {
                    "id": t.id,
                    "title": getattr(t, "name", ""),
                    "artist": orig_artist,
                    "album": orig_album,
                    "album_release_date": orig_release_date,
                    "duration_seconds": getattr(t, "duration", None),
                    "isrc": getattr(t, "isrc", None),
                    "album_upc": orig_upc,
                    "available": available,
                },
                "candidate_count": len(candidates),
                "best_candidate": best_candidate_dict,
                "all_candidates": cand_dicts,
                "replacement_status": "pending",
            }

            # Check if candidate qualifies for replacement
            can_replace = False
            if best_candidate_dict and best_candidate_dict["meets_threshold"]:
                if best_candidate_dict["requires_manual_approval"]:
                    track_entry["replacement_status"] = "skipped_manual_approval_required"
                else:
                    can_replace = True

            if can_replace:
                if not dry_run and isinstance(playlist, UserPlaylist):
                    try:
                        new_track_id = str(best_candidate_dict["id"])
                        # Add substitute at position with allow_duplicates=True
                        added = playlist.add([new_track_id], position=idx, allow_duplicates=True)
                        if not added:
                            logger.error(
                                f"Tidal API did not add candidate ID {new_track_id} for track #{idx} '{t.name}'"
                            )
                            track_entry["replacement_status"] = "error: tidal_skipped_add"
                        else:
                            # Remove old track at shifted index
                            removed = playlist.remove_by_index(idx + 1)
                            if not removed:
                                logger.error(f"Failed to remove old track at index {idx+1}")
                                track_entry["replacement_status"] = "error: failed_to_remove_old_track"
                            else:
                                track_entry["replacement_status"] = "replaced"
                                replacements_applied += 1
                                logger.info(
                                    f"Replaced track #{idx} '{t.name}' with candidate ID {new_track_id} (Score: {best_candidate_dict['similarity_score']})"
                                )
                    except Exception as e:
                        logger.error(f"Failed to replace track #{idx}: {e}")
                        track_entry["replacement_status"] = f"error: {e}"
                else:
                    track_entry["replacement_status"] = "would_replace" if not dry_run else "dry_run_eligible"
            elif not best_candidate_dict or not best_candidate_dict["meets_threshold"]:
                track_entry["replacement_status"] = "no_candidate_meets_threshold"

            unavailable_reports.append(track_entry)

        # Build full report
        timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
        report = {
            "report_generated_at": timestamp,
            "playlist_id": str(playlist.id),
            "playlist_name": playlist.name,
            "total_tracks": len(tracks),
            "unavailable_tracks_count": len(unavailable_reports),
            "threshold_configured": threshold,
            "dry_run": dry_run,
            "replacements_applied": replacements_applied,
            "unavailable_tracks": unavailable_reports,
        }

        # Determine export file path
        if export_path:
            out_file = Path(export_path)
        else:
            reports_dir = self.project_root / "reports"
            reports_dir.mkdir(parents=True, exist_ok=True)
            slug = _slugify(playlist.name)
            date_tag = datetime.date.today().isoformat()
            out_file = reports_dir / f"repair_{slug}_{date_tag}.json"

        out_file.parent.mkdir(parents=True, exist_ok=True)
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)

        report["exported_to"] = str(out_file)
        return report

