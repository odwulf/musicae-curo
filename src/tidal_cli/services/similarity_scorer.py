"""Similarity scoring and explainable reasoning engine for track replacement candidates."""

from __future__ import annotations

import datetime
from typing import Any, Dict, Optional, Set, Tuple
from tidalapi.media import Track

from tidal_cli.services.track_matcher import (
    compute_string_similarity,
    detect_version_tags,
    normalize_title,
)


def _is_mock(val: Any) -> bool:
    return hasattr(val, "_mock_return_value") or hasattr(val, "_mock_wraps") or "Mock" in type(val).__name__


def _clean_str(val: Any) -> Optional[str]:
    if val is None or _is_mock(val):
        return None
    if isinstance(val, (datetime.datetime, datetime.date)):
        return val.isoformat()
    return str(val)


def _extract_date_str(val: Any) -> Optional[str]:
    """Extract YYYY-MM-DD from date, datetime, or string."""
    if val is None or _is_mock(val):
        return None
    if isinstance(val, (datetime.datetime, datetime.date)):
        return val.strftime("%Y-%m-%d")
    s = str(val).strip()
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    return s[:10] if len(s) >= 10 else None


def _extract_year(val: Any) -> Optional[int]:
    """Extract 4-digit year from date or date string."""
    d = _extract_date_str(val)
    if d and len(d) >= 4 and d[:4].isdigit():
        try:
            return int(d[:4])
        except ValueError:
            return None
    return None


def _extract_artists_info(track: Any) -> Tuple[Optional[int], str, Set[int], Set[str]]:
    """Extract primary artist id/name and set of all contributing artist ids/names."""
    primary_art = getattr(track, "artist", None)
    p_id = getattr(primary_art, "id", None)
    if _is_mock(p_id):
        p_id = None
    p_name = getattr(primary_art, "name", "") or ""
    if _is_mock(p_name):
        p_name = ""

    all_ids: Set[int] = set()
    all_names: Set[str] = set()

    if p_id is not None:
        all_ids.add(p_id)
    if p_name:
        all_names.add(normalize_title(p_name))

    artists_list = getattr(track, "artists", None)
    if isinstance(artists_list, (list, tuple)):
        for a in artists_list:
            a_id = getattr(a, "id", None)
            if a_id is not None and not _is_mock(a_id):
                all_ids.add(a_id)
            a_name = getattr(a, "name", "") or ""
            if a_name and not _is_mock(a_name):
                all_names.add(normalize_title(a_name))

    return p_id, p_name, all_ids, all_names


class CandidateEvaluation:
    """Holds computed score, factors, and human-readable reasoning for a candidate track."""

    def __init__(
        self,
        candidate_track: Track,
        score: float,
        reasoning: str,
        isrc_match: bool,
        upc_match: bool,
        title_similarity: float,
        duration_delta: int,
        release_date_match: bool = False,
        requires_manual_approval: bool = False,
        disqualified: bool = False,
    ) -> None:
        self.candidate = candidate_track
        self.score = round(max(0.0, min(1.0, score)), 4)
        self.reasoning = reasoning
        self.isrc_match = isrc_match
        self.upc_match = upc_match
        self.title_similarity = round(title_similarity, 3)
        self.duration_delta = duration_delta
        self.release_date_match = release_date_match
        self.requires_manual_approval = requires_manual_approval
        self.disqualified = disqualified

    def to_dict(self) -> Dict[str, Any]:
        """Convert evaluation to serializable dictionary."""
        c = self.candidate
        cand_artist = getattr(getattr(c, "artist", None), "name", "Unknown Artist")
        cand_album = getattr(getattr(c, "album", None), "name", "Unknown Album")
        cand_upc = getattr(getattr(c, "album", None), "upc", None)
        raw_release_date = getattr(getattr(c, "album", None), "release_date", None)
        cand_release_date = _clean_str(raw_release_date)

        return {
            "id": c.id,
            "title": getattr(c, "name", ""),
            "artist": cand_artist,
            "album": cand_album,
            "album_release_date": cand_release_date,
            "duration_seconds": getattr(c, "duration", None),
            "isrc": getattr(c, "isrc", None),
            "album_upc": cand_upc,
            "similarity_score": self.score,
            "meets_threshold": False,  # Evaluated by caller against configured threshold
            "requires_manual_approval": self.requires_manual_approval,
            "disqualified": self.disqualified,
            "reasoning": self.reasoning,
            "breakdown": {
                "isrc_match": self.isrc_match,
                "upc_match": self.upc_match,
                "release_date_match": self.release_date_match,
                "title_similarity": self.title_similarity,
                "duration_delta_seconds": self.duration_delta,
            },
        }


def evaluate_candidate(
    original: Track,
    candidate: Track,
    original_upc: Optional[str] = None,
    candidate_upc: Optional[str] = None,
    original_release_date: Optional[Any] = None,
    candidate_release_date: Optional[Any] = None,
) -> CandidateEvaluation:
    """Evaluate candidate against original track, producing similarity score and reasoning."""
    reasoning_parts = []
    points = 0.0

    # -----------------------------------------------------------------------
    # Core Metadata Matching (Base: up to 80 points)
    # -----------------------------------------------------------------------

    # 1. Title similarity (up to 30 points)
    orig_title = getattr(original, "name", "") or ""
    cand_title = getattr(candidate, "name", "") or ""
    title_sim = compute_string_similarity(orig_title, cand_title)
    points += title_sim * 30.0

    if title_sim >= 0.95:
        reasoning_parts.append("Title is an exact normalized match (+30 pts).")
    elif title_sim >= 0.8:
        reasoning_parts.append(f"Title is very similar ({round(title_sim*100)}% match, +{round(title_sim*30, 1)} pts).")
    else:
        reasoning_parts.append(f"Title match is weak ({round(title_sim*100)}% match, +{round(title_sim*30, 1)} pts).")

    # 2. Artist matching (up to 25 points)
    orig_p_id, orig_p_name, orig_ids, orig_names = _extract_artists_info(original)
    cand_p_id, cand_p_name, cand_ids, cand_names = _extract_artists_info(candidate)

    # Immediate rejection of tribute, karaoke, or generic cover artists
    cand_art_lower = cand_p_name.lower()
    if any(k in cand_art_lower for k in ("tribute", "karaoke", "cover band", "instrumental band")):
        reasoning_parts.append(f"[DISQUALIFIED] Tribute / Karaoke artist detected ('{cand_p_name}').")
        return CandidateEvaluation(
            candidate_track=candidate,
            score=0.05,
            reasoning=" ".join(reasoning_parts),
            isrc_match=False,
            upc_match=False,
            title_similarity=title_sim,
            duration_delta=abs((getattr(original, "duration", 0) or 0) - (getattr(candidate, "duration", 0) or 0)),
            requires_manual_approval=True,
            disqualified=True,
        )

    if orig_p_id and cand_p_id and orig_p_id == cand_p_id:
        points += 25.0
        reasoning_parts.append(f"Exact primary artist match (ID: {orig_p_id}, +25 pts).")
    elif orig_ids and cand_ids and (orig_ids & cand_ids):
        if orig_ids == cand_ids or (orig_p_id in cand_ids and cand_p_id in orig_ids):
            points += 25.0
            reasoning_parts.append("Exact collaborating artists match (+25 pts).")
        elif orig_p_id in cand_ids:
            points += 24.0
            reasoning_parts.append(f"Original primary artist present in candidate collaborators (+24 pts).")
        else:
            points += 22.0
            reasoning_parts.append(f"Shared contributing artist ID match (+22 pts).")
    elif orig_p_name and cand_p_name and normalize_title(orig_p_name) == normalize_title(cand_p_name):
        points += 24.0
        reasoning_parts.append(f"Artist names match ('{orig_p_name}', +24 pts).")
    elif orig_names and cand_names and (orig_names & cand_names):
        points += 24.0
        reasoning_parts.append(f"Shared collaborating artist name match (+24 pts).")
    else:
        art_sim = compute_string_similarity(orig_p_name, cand_p_name)
        if (orig_p_id and cand_p_id and orig_p_id != cand_p_id and art_sim < 0.6) or art_sim < 0.5:
            reasoning_parts.append(f"[DISQUALIFIED] Artist mismatch: '{cand_p_name}' vs '{orig_p_name}'.")
            return CandidateEvaluation(
                candidate_track=candidate,
                score=0.05,
                reasoning=" ".join(reasoning_parts),
                isrc_match=False,
                upc_match=False,
                title_similarity=title_sim,
                duration_delta=abs((getattr(original, "duration", 0) or 0) - (getattr(candidate, "duration", 0) or 0)),
                requires_manual_approval=True,
                disqualified=True,
            )
        art_pts = art_sim * 20.0
        points += art_pts
        reasoning_parts.append(f"Artist names differ slightly ('{cand_p_name}' vs '{orig_p_name}', +{round(art_pts, 1)} pts).")

    # 3. Duration delta (up to 15 points)
    orig_dur = getattr(original, "duration", 0) or 0
    cand_dur = getattr(candidate, "duration", 0) or 0
    duration_delta = abs(orig_dur - cand_dur)

    if duration_delta <= 1:
        points += 15.0
        reasoning_parts.append(f"Duration is identical (diff: {duration_delta}s, +15 pts).")
    elif duration_delta <= 3:
        points += 12.0
        reasoning_parts.append(f"Duration is virtually identical (diff: {duration_delta}s, +12 pts).")
    elif duration_delta <= 7:
        points += 7.0
        reasoning_parts.append(f"Duration is close (diff: {duration_delta}s, +7 pts).")
    elif duration_delta <= 15:
        points += 2.0
        reasoning_parts.append(f"Duration differs moderately (diff: {duration_delta}s, +2 pts).")
    else:
        reasoning_parts.append(f"Duration differs significantly (diff: {duration_delta}s, +0 pts).")

    # 4. Album name match (up to 10 points)
    orig_alb_name = getattr(getattr(original, "album", None), "name", "") or ""
    cand_alb_name = getattr(getattr(candidate, "album", None), "name", "") or ""
    clean_orig_alb = normalize_title(orig_alb_name)
    clean_cand_alb = normalize_title(cand_alb_name)
    alb_sim = max(
        compute_string_similarity(orig_alb_name, cand_alb_name),
        compute_string_similarity(clean_orig_alb, clean_cand_alb),
    )

    if alb_sim >= 0.9:
        points += 10.0
        reasoning_parts.append(f"Same album release ('{cand_alb_name}', +10 pts).")
    elif alb_sim >= 0.5:
        alb_pts = round(alb_sim * 8.0, 1)
        points += alb_pts
        reasoning_parts.append(f"Similar album title ('{cand_alb_name}' vs '{orig_alb_name}', +{alb_pts} pts).")
    elif clean_orig_alb and clean_orig_alb == normalize_title(orig_title):
        # Original was a self-titled single release (e.g. single absorbed into full album)
        points += 8.0
        reasoning_parts.append(
            f"Original was a single release ('{orig_alb_name}'); candidate is on full album ('{cand_alb_name}', +8 pts)."
        )
    else:
        reasoning_parts.append(f"Different album ('{cand_alb_name}' vs '{orig_alb_name}', +0 pts).")

    # -----------------------------------------------------------------------
    # Identifier Bonuses (ISRC: +10 pts, Album UPC: +10 pts -> Max 100%)
    # -----------------------------------------------------------------------

    # 5. ISRC match bonus (+10 points)
    orig_isrc = (getattr(original, "isrc", None) or "").strip().upper()
    cand_isrc = (getattr(candidate, "isrc", None) or "").strip().upper()
    isrc_match = bool(orig_isrc and cand_isrc and orig_isrc == cand_isrc)

    if isrc_match:
        points += 10.0
        reasoning_parts.append(f"Exact ISRC match confirmed ({orig_isrc}, +10 pts).")
    elif orig_isrc and cand_isrc:
        reasoning_parts.append(f"ISRC differs (orig: {orig_isrc}, cand: {cand_isrc}).")
    else:
        reasoning_parts.append("ISRC not available on original (relying on metadata).")

    # 6. Album UPC match bonus (+10 points)
    o_upc = (original_upc or getattr(getattr(original, "album", None), "upc", "") or "").strip()
    c_upc = (candidate_upc or getattr(getattr(candidate, "album", None), "upc", "") or "").strip()
    upc_match = bool(o_upc and c_upc and o_upc == c_upc)

    if upc_match:
        points += 10.0
        reasoning_parts.append(f"Identical album UPC barcode confirmed ({o_upc}, +10 pts).")
    elif o_upc and c_upc:
        reasoning_parts.append(f"Album UPC differs (orig: {o_upc}, cand: {c_upc}).")
    else:
        reasoning_parts.append("UPC not available on original release.")

    # 7. Album Release Date match (up to +5 points bonus)
    raw_orig_date = original_release_date or getattr(getattr(original, "album", None), "release_date", None)
    raw_cand_date = candidate_release_date or getattr(getattr(candidate, "album", None), "release_date", None)
    orig_date_str = _extract_date_str(raw_orig_date)
    cand_date_str = _extract_date_str(raw_cand_date)
    orig_year = _extract_year(raw_orig_date)
    cand_year = _extract_year(raw_cand_date)

    release_date_match = False
    if orig_date_str and cand_date_str and orig_date_str == cand_date_str:
        points += 5.0
        release_date_match = True
        reasoning_parts.append(f"Identical album release date confirmed ({orig_date_str}, +5 pts).")
    elif orig_year and cand_year and orig_year == cand_year:
        points += 2.0
        reasoning_parts.append(f"Same album release year ({orig_year}, +2 pts).")
    elif orig_date_str and cand_date_str:
        reasoning_parts.append(f"Album release date differs (orig: {orig_date_str}, cand: {cand_date_str}).")

    # -----------------------------------------------------------------------
    # Version Mismatch Check (Live, Acoustic, Remix, Instrumental)
    # -----------------------------------------------------------------------
    orig_tags = detect_version_tags(orig_title, orig_alb_name)
    cand_tags = detect_version_tags(cand_title, cand_alb_name)

    requires_manual_approval = False
    raw_score = points / 100.0

    # Severe penalty if candidate is live but original is not
    if "live" not in orig_tags and "live" in cand_tags:
        raw_score = min(raw_score * 0.4, 0.35)
        requires_manual_approval = True
        reasoning_parts.append(
            "[HEAVILY PENALIZED] Candidate is a Live recording while original is a studio version. "
            "Requires manual approval; will not replace automatically."
        )

    # Severe penalty if candidate is acoustic but original is not
    if "acoustic" not in orig_tags and "acoustic" in cand_tags:
        raw_score = min(raw_score * 0.5, 0.40)
        requires_manual_approval = True
        reasoning_parts.append(
            "[HEAVILY PENALIZED] Candidate is an Acoustic version while original is not. "
            "Requires manual approval."
        )

    # Severe penalty if candidate is instrumental / karaoke but original is not
    if "instrumental" not in orig_tags and "instrumental" in cand_tags:
        raw_score = min(raw_score * 0.2, 0.20)
        requires_manual_approval = True
        reasoning_parts.append(
            "[HEAVILY PENALIZED] Candidate is Instrumental / Karaoke. Requires manual approval."
        )

    # Penalty if candidate is a remix but original is not
    if "remix" not in orig_tags and "remix" in cand_tags:
        raw_score = min(raw_score * 0.6, 0.50)
        requires_manual_approval = True
        reasoning_parts.append(
            "[PENALIZED] Candidate is a Remix while original was not. Requires manual approval."
        )

    return CandidateEvaluation(
        candidate_track=candidate,
        score=raw_score,
        reasoning=" ".join(reasoning_parts),
        isrc_match=isrc_match,
        upc_match=upc_match,
        title_similarity=title_sim,
        duration_delta=duration_delta,
        release_date_match=release_date_match,
        requires_manual_approval=requires_manual_approval,
        disqualified=False,
    )