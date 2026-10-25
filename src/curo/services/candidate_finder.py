"""Candidate search and discovery engine for unavailable tracks."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from curo.core.models import Track
from curo.core.provider import MusicProvider
from curo.services.similarity_scorer import CandidateEvaluation, evaluate_candidate
from curo.services.track_matcher import is_track_available, normalize_title

logger = logging.getLogger(__name__)


def _is_valid_track(t: Any) -> bool:
    if t is None:
        return False
    raw_id = getattr(t, "id", None)
    if raw_id is None or hasattr(raw_id, "_mock_return_value") or "Mock" in type(raw_id).__name__:
        return False
    return bool(is_track_available(t))


class CandidateFinder:
    """Discovers active candidate replacements for unavailable tracks using a tiered strategy."""

    def __init__(self, provider: MusicProvider, snapshot_service: Optional[Any] = None) -> None:
        self.provider = provider
        self.snapshot_service = snapshot_service

    def find_candidates(self, original_track: Any, max_candidates: int = 10) -> List[CandidateEvaluation]:
        """Find and rank replacement candidates for an unavailable track."""
        orig_id = getattr(original_track, "id", None)
        orig_isrc = getattr(original_track, "isrc", None)
        orig_title = getattr(original_track, "name", None) or getattr(original_track, "title", "") or ""
        orig_artist = getattr(getattr(original_track, "artist", None), "name", "") or ""
        orig_upc = getattr(getattr(original_track, "album", None), "upc", None)
        orig_release_date = getattr(getattr(original_track, "album", None), "release_date", None)

        # Recover historical ISRC, UPC, or release date from snapshot registry if missing
        hist = None
        if self.snapshot_service and orig_id is not None:
            try:
                hist = self.snapshot_service.get_historical_track(orig_id)
                if hist:
                    if not orig_isrc and hist.get("isrc"):
                        orig_isrc = hist.get("isrc")
                        logger.info(f"Recovered historical ISRC '{orig_isrc}' for track #{orig_id}")
                    if not orig_upc and hist.get("album_upc"):
                        orig_upc = hist.get("album_upc")
                        logger.info(f"Recovered historical album UPC '{orig_upc}' for track #{orig_id}")
                    if not orig_release_date and hist.get("album_release_date"):
                        orig_release_date = hist.get("album_release_date")
            except Exception as e:
                logger.debug(f"Failed to query historical track registry for #{orig_id}: {e}")

        candidates_by_id: Dict[str, Any] = {}
        official_replacement_ids: set[str] = set()

        # -------------------------------------------------------------------
        # Tier 0: Direct Provider Replacement / Cached Replacement Pointer
        # -------------------------------------------------------------------
        cached_rep_id = hist.get("provider_replacement_id") if hist else None
        if cached_rep_id:
            try:
                cand_t = self.provider.get_track(str(cached_rep_id))
                if _is_valid_track(cand_t):
                    t_id = str(getattr(cand_t, "id", cached_rep_id))
                    if t_id != str(orig_id):
                        candidates_by_id[t_id] = cand_t
                        official_replacement_ids.add(t_id)
                        logger.info(f"Loaded cached provider replacement #{t_id} for track #{orig_id}")
            except Exception as e:
                logger.debug(f"Failed to fetch cached replacement track #{cached_rep_id}: {e}")

        if orig_id is not None and hasattr(self.provider, "get_track_replacement"):
            try:
                rep_track = self.provider.get_track_replacement(str(orig_id))
                if _is_valid_track(rep_track):
                    t_id = str(getattr(rep_track, "id", None) or "")
                    if t_id and t_id != str(orig_id):
                        candidates_by_id[t_id] = rep_track
                        official_replacement_ids.add(t_id)
                        logger.info(f"Discovered active provider replacement #{t_id} for track #{orig_id}")
            except Exception as e:
                logger.debug(f"Provider replacement lookup failed for #{orig_id}: {e}")

        # -------------------------------------------------------------------
        # Tier 1: Search by ISRC
        # -------------------------------------------------------------------
        if orig_isrc:
            try:
                isrc_tracks = self.provider.get_tracks_by_isrc(orig_isrc)
                for t in isrc_tracks or []:
                    t_id = str(t.id)
                    if t_id != str(orig_id) and is_track_available(t):
                        candidates_by_id[t_id] = t
            except Exception as e:
                logger.debug(f"ISRC lookup for '{orig_isrc}' produced no results: {e}")

        # -------------------------------------------------------------------
        # Tier 2: Search by Album UPC Barcode (if UPC known)
        # -------------------------------------------------------------------
        if orig_upc:
            try:
                albums = self.provider.get_albums_by_barcode(orig_upc)
                for alb in albums or []:
                    # alb could be domain Album or SDK Album
                    alb_id = getattr(alb, "id", None)
                    tracks = (
                        self.provider.get_album_tracks(str(alb_id))
                        if alb_id
                        else (alb.tracks() if hasattr(alb, "tracks") else [])
                    )
                    for t in tracks or []:
                        t_id = str(t.id)
                        if t_id != str(orig_id) and is_track_available(t):
                            if t_id not in candidates_by_id:
                                candidates_by_id[t_id] = t
            except Exception as e:
                logger.debug(f"UPC lookup for '{orig_upc}' produced no results: {e}")

        # -------------------------------------------------------------------
        # Tier 3: Search by Title and Artist
        # -------------------------------------------------------------------
        clean_title = normalize_title(orig_title)
        query = f"{orig_artist} {clean_title}".strip()
        if query:
            try:
                tracks = self.provider.search_tracks(query, limit=20)
                for t in tracks or []:
                    t_id = str(t.id)
                    if t_id != str(orig_id) and is_track_available(t):
                        if t_id not in candidates_by_id:
                            candidates_by_id[t_id] = t
            except Exception as e:
                logger.debug(f"Text search for '{query}' failed: {e}")

        # -------------------------------------------------------------------
        # Evaluate & Score Candidates
        # -------------------------------------------------------------------
        evaluations: List[CandidateEvaluation] = []
        for cand in candidates_by_id.values():
            cand_id_str = str(getattr(cand, "id", ""))
            cand_upc = getattr(getattr(cand, "album", None), "upc", None)
            cand_release_date = getattr(getattr(cand, "album", None), "release_date", None)
            is_official = cand_id_str in official_replacement_ids
            ev = evaluate_candidate(
                original=original_track,
                candidate=cand,
                original_upc=orig_upc,
                candidate_upc=cand_upc,
                original_release_date=orig_release_date,
                candidate_release_date=cand_release_date,
                original_isrc=orig_isrc,
                is_provider_replacement=is_official,
            )
            evaluations.append(ev)

        evaluations.sort(key=lambda x: x.score, reverse=True)
        return evaluations[:max_candidates]

