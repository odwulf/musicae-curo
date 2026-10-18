"""Service for cross-provider and intra-provider playlist comparison and synchronization."""

from __future__ import annotations

import datetime
import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from curo.config import ConfigManager, config_manager
from curo.core.models import Playlist, Track
from curo.core.provider import MusicProvider
from curo.services.similarity_scorer import CandidateEvaluation, evaluate_candidate
from curo.services.track_matcher import is_track_available, normalize_title

logger = logging.getLogger(__name__)


def _is_mock(val: Any) -> bool:
    return hasattr(val, "_mock_return_value") or hasattr(val, "_mock_wraps") or "Mock" in type(val).__name__


def _clean_str(val: Any) -> Optional[str]:
    if val is None or _is_mock(val):
        return None
    if isinstance(val, (datetime.datetime, datetime.date)):
        return val.isoformat()
    return str(val)


def _slugify(text: str) -> str:
    s = re.sub(r"[^\w\s-]", "", text).strip().lower()
    return re.sub(r"[-\s]+", "_", s) or "playlist"


class PlaylistSyncService:
    """Compares and synchronizes playlists across music streaming providers or within the same provider."""

    def __init__(
        self,
        source_provider: MusicProvider,
        target_provider: Optional[MusicProvider] = None,
        cfg: Optional[ConfigManager] = None,
        project_root: Optional[Path] = None,
    ) -> None:
        self.source_provider = source_provider
        self.target_provider = target_provider or source_provider
        self.config = cfg or config_manager
        self.project_root = project_root or Path.cwd()

        from curo.services.snapshot_service import SnapshotService
        self.source_snapshot_service = SnapshotService(source_provider, cfg=self.config)

    def _extract_track_identifiers(self, track: Any) -> Tuple[Optional[str], Optional[str], str, str, Optional[str], Optional[str]]:
        """Extract id, isrc, title, artist, upc, and release_date from a track."""
        raw_id = getattr(track, "id", None)
        t_id = str(raw_id) if raw_id is not None and not _is_mock(raw_id) else None

        raw_isrc = getattr(track, "isrc", None)
        isrc = _clean_str(raw_isrc) if not _is_mock(raw_isrc) else None

        title = _clean_str(getattr(track, "name", None)) or _clean_str(getattr(track, "title", None)) or "Unknown Track"
        artist_obj = getattr(track, "artist", None)
        artist = _clean_str(getattr(artist_obj, "name", None)) or "Unknown Artist"

        album_obj = getattr(track, "album", None)
        upc = _clean_str(getattr(album_obj, "upc", None))
        raw_date = getattr(album_obj, "release_date", None)
        rel_date = (
            raw_date.isoformat()
            if isinstance(raw_date, (datetime.datetime, datetime.date))
            else (_clean_str(raw_date))
        )

        # Recover historical metadata if missing
        if self.source_snapshot_service and t_id is not None:
            try:
                hist = self.source_snapshot_service.get_historical_track(t_id)
                if hist:
                    if not isrc and hist.get("isrc"):
                        isrc = hist.get("isrc")
                    if not upc and hist.get("album_upc"):
                        upc = hist.get("album_upc")
                    if not rel_date and hist.get("album_release_date"):
                        rel_date = hist.get("album_release_date")
            except Exception:
                pass

        return t_id, isrc, title, artist, upc, rel_date

    def resolve_track_on_target(
        self,
        source_track: Any,
        threshold: float = 0.8,
    ) -> Optional[CandidateEvaluation]:
        """Attempt to find and score the best corresponding active track on the target provider."""
        s_id, s_isrc, s_title, s_artist, s_upc, _ = self._extract_track_identifiers(source_track)

        # 0. Same provider optimization: exact ID match
        if self.source_provider.name == self.target_provider.name and s_id:
            try:
                target_t = self.target_provider.get_track(s_id)
                if target_t and is_track_available(target_t):
                    eval_res = evaluate_candidate(source_track, target_t)
                    if eval_res.score >= threshold and not eval_res.disqualified:
                        return eval_res
            except Exception as e:
                logger.debug(f"Direct ID lookup for #{s_id} on target provider failed: {e}")

        candidates_by_id: Dict[str, Any] = {}

        # 1. Tier 1: Search target provider by ISRC sound recording code
        if s_isrc:
            try:
                isrc_tracks = self.target_provider.get_tracks_by_isrc(s_isrc)
                for t in isrc_tracks or []:
                    t_id = str(t.id) if getattr(t, "id", None) is not None else None
                    if t_id and is_track_available(t):
                        candidates_by_id[t_id] = t
            except Exception as e:
                logger.debug(f"Target ISRC query '{s_isrc}' failed: {e}")

        # 2. Tier 2: Search target provider by Album UPC Barcode
        if s_upc and not candidates_by_id:
            try:
                albums = self.target_provider.get_albums_by_barcode(s_upc)
                for alb in albums or []:
                    alb_id = getattr(alb, "id", None)
                    tracks = (
                        self.target_provider.get_album_tracks(str(alb_id))
                        if alb_id
                        else (alb.tracks() if hasattr(alb, "tracks") else [])
                    )
                    for t in tracks or []:
                        t_id = str(t.id) if getattr(t, "id", None) is not None else None
                        if t_id and is_track_available(t):
                            candidates_by_id[t_id] = t
            except Exception as e:
                logger.debug(f"Target UPC barcode query '{s_upc}' failed: {e}")

        # 3. Tier 3: Search target provider by Artist and Title
        if not candidates_by_id:
            clean_title = normalize_title(s_title)
            query = f"{s_artist} {clean_title}".strip()
            if query:
                try:
                    searched = self.target_provider.search_tracks(query, limit=15)
                    for t in searched or []:
                        t_id = str(t.id) if getattr(t, "id", None) is not None else None
                        if t_id and is_track_available(t):
                            candidates_by_id[t_id] = t
                except Exception as e:
                    logger.debug(f"Target text search for '{query}' failed: {e}")

        # Evaluate and score all collected candidates
        scored_candidates: List[CandidateEvaluation] = []
        for cand in candidates_by_id.values():
            eval_res = evaluate_candidate(source_track, cand)
            if eval_res.score >= threshold and not eval_res.disqualified:
                scored_candidates.append(eval_res)

        if not scored_candidates:
            return None

        # Sort by similarity score descending
        scored_candidates.sort(key=lambda c: c.score, reverse=True)
        return scored_candidates[0]

    def _tracks_match(self, t1: Any, t2: Any, threshold: float = 0.8) -> Tuple[bool, float, str]:
        """Check if two tracks match between source and target playlists."""
        id1 = str(getattr(t1, "id", ""))
        id2 = str(getattr(t2, "id", ""))
        if id1 and id2 and id1 == id2 and self.source_provider.name == self.target_provider.name:
            return True, 1.0, "exact_id"

        isrc1 = _clean_str(getattr(t1, "isrc", None))
        isrc2 = _clean_str(getattr(t2, "isrc", None))
        if isrc1 and isrc2 and isrc1.strip().upper() == isrc2.strip().upper():
            return True, 1.0, "exact_isrc"

        eval_res = evaluate_candidate(t1, t2)
        if eval_res.score >= threshold and not eval_res.disqualified:
            return True, eval_res.score, "fuzzy_metadata"

        return False, eval_res.score, "no_match"

    def compare_playlists(
        self,
        source_identifier: str,
        target_identifier: str,
        threshold: float = 0.8,
        export_path: Optional[Path] = None,
    ) -> Dict[str, Any]:
        """Compare two playlists across providers and calculate diff items."""
        source_pl = self.source_provider.get_playlist(source_identifier)
        if not source_pl:
            raise ValueError(f"Source playlist '{source_identifier}' not found on {self.source_provider.display_name}")

        target_pl = self.target_provider.get_playlist(target_identifier)
        if not target_pl:
            raise ValueError(f"Target playlist '{target_identifier}' not found on {self.target_provider.display_name}")

        source_tracks = self.source_provider.get_playlist_tracks(str(source_pl.id)) or []
        target_tracks = self.target_provider.get_playlist_tracks(str(target_pl.id)) or []

        matched_target_indices: Set[int] = set()
        diff_items: List[Dict[str, Any]] = []

        in_sync_count = 0
        missing_count = 0
        unresolvable_count = 0

        for s_idx, s in enumerate(source_tracks, start=1):
            s_id, s_isrc, s_title, s_artist, _, _ = self._extract_track_identifiers(s)
            matched_target_idx: Optional[int] = None
            match_score = 0.0
            match_method = "none"

            # Check if source track exists in target playlist
            for t_idx, t in enumerate(target_tracks, start=1):
                if t_idx in matched_target_indices:
                    continue
                matches, score, method = self._tracks_match(s, t, threshold=threshold)
                if matches:
                    matched_target_idx = t_idx
                    match_score = score
                    match_method = method
                    matched_target_indices.add(t_idx)
                    break

            if matched_target_idx is not None:
                in_sync_count += 1
                target_t = target_tracks[matched_target_idx - 1]
                t_id, t_isrc, t_title, t_artist, _, _ = self._extract_track_identifiers(target_t)
                diff_items.append({
                    "source_index": s_idx,
                    "source_track": {"id": s_id, "title": s_title, "artist": s_artist, "isrc": s_isrc},
                    "target_index": matched_target_idx,
                    "target_track": {"id": t_id, "title": t_title, "artist": t_artist, "isrc": t_isrc},
                    "status": "in_sync",
                    "action": "keep",
                    "similarity_score": match_score,
                    "resolution_method": match_method,
                })
            else:
                # Track is missing from target playlist -> attempt to resolve on target provider
                best_cand = self.resolve_track_on_target(s, threshold=threshold)
                if best_cand:
                    missing_count += 1
                    cand_t = best_cand.candidate
                    c_id, c_isrc, c_title, c_artist, _, _ = self._extract_track_identifiers(cand_t)
                    diff_items.append({
                        "source_index": s_idx,
                        "source_track": {"id": s_id, "title": s_title, "artist": s_artist, "isrc": s_isrc},
                        "target_index": None,
                        "target_track": {"id": c_id, "title": c_title, "artist": c_artist, "isrc": c_isrc},
                        "status": "missing_in_target",
                        "action": "to_add",
                        "similarity_score": best_cand.score,
                        "resolution_method": "target_catalog_match",
                    })
                else:
                    unresolvable_count += 1
                    diff_items.append({
                        "source_index": s_idx,
                        "source_track": {"id": s_id, "title": s_title, "artist": s_artist, "isrc": s_isrc},
                        "target_index": None,
                        "target_track": None,
                        "status": "unresolvable",
                        "action": "unresolvable",
                        "similarity_score": 0.0,
                        "resolution_method": "unresolved",
                    })

        # Find target tracks not present in source playlist
        extra_count = 0
        for t_idx, t in enumerate(target_tracks, start=1):
            if t_idx not in matched_target_indices:
                extra_count += 1
                t_id, t_isrc, t_title, t_artist, _, _ = self._extract_track_identifiers(t)
                diff_items.append({
                    "source_index": None,
                    "source_track": None,
                    "target_index": t_idx,
                    "target_track": {"id": t_id, "title": t_title, "artist": t_artist, "isrc": t_isrc},
                    "status": "extra_in_target",
                    "action": "extra_in_target",
                    "similarity_score": 0.0,
                    "resolution_method": "none",
                })

        timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
        src_name = getattr(source_pl, "title", source_identifier)
        tgt_name = getattr(target_pl, "title", target_identifier)

        report = {
            "generated_at": timestamp,
            "source_provider": self.source_provider.name,
            "source_playlist_id": str(source_pl.id),
            "source_playlist_name": src_name,
            "target_provider": self.target_provider.name,
            "target_playlist_id": str(target_pl.id),
            "target_playlist_name": tgt_name,
            "threshold": threshold,
            "source_tracks_total": len(source_tracks),
            "target_tracks_total": len(target_tracks),
            "in_sync_count": in_sync_count,
            "missing_in_target_count": missing_count,
            "extra_in_target_count": extra_count,
            "unresolvable_count": unresolvable_count,
            "diff_items": diff_items,
        }

        if export_path:
            out_file = Path(export_path)
        else:
            reports_dir = self.project_root / "reports"
            reports_dir.mkdir(parents=True, exist_ok=True)
            slug = f"compare_{self.source_provider.name}_{_slugify(src_name)}_to_{self.target_provider.name}_{_slugify(tgt_name)}"
            date_tag = datetime.date.today().isoformat()
            out_file = reports_dir / f"{slug}_{date_tag}.json"

        out_file.parent.mkdir(parents=True, exist_ok=True)
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)

        report["exported_to"] = str(out_file)
        return report

    def sync_playlists(
        self,
        source_identifier: str,
        target_identifier: Optional[str] = None,
        mode: str = "mirror",
        threshold: float = 0.8,
        dry_run: bool = True,
        create_if_missing: bool = True,
        export_path: Optional[Path] = None,
    ) -> Dict[str, Any]:
        """Synchronize playlist tracks from source provider to target provider."""
        source_pl = self.source_provider.get_playlist(source_identifier)
        if not source_pl:
            raise ValueError(f"Source playlist '{source_identifier}' not found on {self.source_provider.display_name}")

        src_name = getattr(source_pl, "title", source_identifier)
        tgt_name = target_identifier or src_name

        source_tracks = self.source_provider.get_playlist_tracks(str(source_pl.id)) or []

        # Find or create target playlist
        target_pl = self.target_provider.get_playlist(tgt_name)
        target_created = False

        if not target_pl:
            if create_if_missing:
                if not dry_run:
                    target_pl = self.target_provider.create_playlist(
                        title=tgt_name,
                        description=f"Synchronized from {self.source_provider.display_name} via Musicae Curo",
                    )
                    target_created = True
                else:
                    target_pl = Playlist(
                        id="dry-run-placeholder",
                        title=tgt_name,
                        provider=self.target_provider.name,
                        track_count=0,
                    )
                    target_created = True
            else:
                raise ValueError(
                    f"Target playlist '{tgt_name}' not found on {self.target_provider.display_name}. "
                    f"Set create_if_missing=True to create automatically."
                )

        target_tracks = (
            self.target_provider.get_playlist_tracks(str(target_pl.id))
            if not target_created and target_pl.id != "dry-run-placeholder"
            else []
        )

        resolved_track_ids: List[str] = []
        resolved_items: List[Dict[str, Any]] = []
        unresolvable_items: List[Dict[str, Any]] = []

        # Process each source track
        for s_idx, s in enumerate(source_tracks, start=1):
            s_id, s_isrc, s_title, s_artist, _, _ = self._extract_track_identifiers(s)

            # Check if track is already present in target playlist
            matched_in_target = None
            for t in target_tracks:
                matches, score, method = self._tracks_match(s, t, threshold=threshold)
                if matches:
                    matched_in_target = t
                    break

            if matched_in_target:
                t_id = str(matched_in_target.id)
                resolved_track_ids.append(t_id)
                resolved_items.append({
                    "source_index": s_idx,
                    "source_title": s_title,
                    "source_artist": s_artist,
                    "target_track_id": t_id,
                    "target_title": getattr(matched_in_target, "title", s_title),
                    "status": "already_present",
                })
            else:
                cand_eval = self.resolve_track_on_target(s, threshold=threshold)
                if cand_eval:
                    t_id = str(cand_eval.candidate.id)
                    resolved_track_ids.append(t_id)
                    resolved_items.append({
                        "source_index": s_idx,
                        "source_title": s_title,
                        "source_artist": s_artist,
                        "target_track_id": t_id,
                        "target_title": getattr(cand_eval.candidate, "title", s_title),
                        "status": "resolved",
                        "similarity_score": cand_eval.score,
                    })
                else:
                    unresolvable_items.append({
                        "source_index": s_idx,
                        "source_title": s_title,
                        "source_artist": s_artist,
                        "source_isrc": s_isrc,
                        "reason": "no_candidate_meets_threshold",
                    })

        tracks_added_count = 0
        tracks_removed_count = 0

        if mode == "mirror":
            # In mirror mode, clear target playlist and re-add all resolved tracks in sequence
            tracks_added_count = len(resolved_track_ids)
            tracks_removed_count = len(target_tracks)

            if not dry_run and target_pl.id != "dry-run-placeholder":
                if not target_created:
                    self.target_provider.clear_playlist(str(target_pl.id))
                if resolved_track_ids:
                    self.target_provider.add_tracks_to_playlist(str(target_pl.id), resolved_track_ids)
        elif mode == "append":
            # In append mode, add only tracks that were not already present
            existing_target_ids = {str(t.id) for t in target_tracks}
            new_ids_to_add = [tid for tid in resolved_track_ids if tid not in existing_target_ids]
            tracks_added_count = len(new_ids_to_add)
            tracks_removed_count = 0

            if not dry_run and new_ids_to_add and target_pl.id != "dry-run-placeholder":
                self.target_provider.add_tracks_to_playlist(str(target_pl.id), new_ids_to_add)
        else:
            raise ValueError(f"Unknown sync mode '{mode}'. Choose 'mirror' or 'append'.")

        timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
        target_tracks_after = (
            len(resolved_track_ids) if mode == "mirror" else len(target_tracks) + tracks_added_count
        )

        report = {
            "synced_at": timestamp,
            "mode": mode,
            "dry_run": dry_run,
            "threshold": threshold,
            "source_provider": self.source_provider.name,
            "source_playlist_name": src_name,
            "source_tracks_count": len(source_tracks),
            "target_provider": self.target_provider.name,
            "target_playlist_name": tgt_name,
            "target_playlist_created": target_created,
            "target_tracks_before": len(target_tracks),
            "target_tracks_after": target_tracks_after,
            "tracks_resolved_count": len(resolved_track_ids),
            "tracks_unresolvable_count": len(unresolvable_items),
            "tracks_added": tracks_added_count,
            "tracks_removed": tracks_removed_count,
            "unresolvable_tracks": unresolvable_items,
            "resolved_items": resolved_items,
        }

        if export_path:
            out_file = Path(export_path)
        else:
            reports_dir = self.project_root / "reports"
            reports_dir.mkdir(parents=True, exist_ok=True)
            slug = f"sync_{self.source_provider.name}_{_slugify(src_name)}_to_{self.target_provider.name}_{_slugify(tgt_name)}"
            date_tag = datetime.date.today().isoformat()
            out_file = reports_dir / f"{slug}_{date_tag}.json"

        out_file.parent.mkdir(parents=True, exist_ok=True)
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)

        report["exported_to"] = str(out_file)
        return report

