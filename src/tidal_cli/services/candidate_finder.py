"""Candidate search and discovery engine for unavailable tracks."""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

import tidalapi
from tidalapi.media import Track

from tidal_cli.services.similarity_scorer import CandidateEvaluation, evaluate_candidate
from tidal_cli.services.track_matcher import is_track_available, normalize_title

logger = logging.getLogger(__name__)


class CandidateFinder:
    """Discovers active candidate replacements for unavailable tracks using a tiered strategy."""

    def __init__(self, session: tidalapi.Session) -> None:
        self.session = session

    def find_candidates(self, original_track: Track, max_candidates: int = 10) -> List[CandidateEvaluation]:
        """Find and rank replacement candidates for an unavailable track."""
        orig_id = getattr(original_track, "id", None)
        orig_isrc = getattr(original_track, "isrc", None)
        orig_title = getattr(original_track, "name", "") or ""
        orig_artist = getattr(getattr(original_track, "artist", None), "name", "") or ""
        orig_upc = getattr(getattr(original_track, "album", None), "upc", None)

        candidates_by_id: Dict[str, Track] = {}

        # -------------------------------------------------------------------
        # Tier 1: Search by ISRC
        # -------------------------------------------------------------------
        if orig_isrc:
            try:
                isrc_tracks = self.session.get_tracks_by_isrc(orig_isrc)
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
                albums = self.session.get_albums_by_barcode(orig_upc)
                for alb in albums or []:
                    for t in alb.tracks() or []:
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
                search_res = self.session.search(query, models=[Track], limit=20)
                tracks = (
                    search_res.get("tracks", [])
                    if isinstance(search_res, dict)
                    else getattr(search_res, "tracks", [])
                )
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
            cand_upc = getattr(getattr(cand, "album", None), "upc", None)
            ev = evaluate_candidate(
                original=original_track,
                candidate=cand,
                original_upc=orig_upc,
                candidate_upc=cand_upc,
            )
            evaluations.append(ev)

        # Sort evaluations by score descending (highest quality candidate first)
        evaluations.sort(key=lambda x: x.score, reverse=True)

        return evaluations[:max_candidates]

