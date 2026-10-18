"""Similarity scoring and explainable reasoning engine for track replacement candidates."""

from __future__ import annotations

from typing import Any, Dict, Optional
from tidalapi.media import Track

from tidal_cli.services.track_matcher import (
    compute_string_similarity,
    detect_version_tags,
    normalize_title,
)


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
        self.requires_manual_approval = requires_manual_approval
        self.disqualified = disqualified

    def to_dict(self) -> Dict[str, Any]:
        """Convert evaluation to serializable dictionary."""
        c = self.candidate
        cand_artist = getattr(getattr(c, "artist", None), "name", "Unknown Artist")
        cand_album = getattr(getattr(c, "album", None), "name", "Unknown Album")
        cand_upc = getattr(getattr(c, "album", None), "upc", None)

        return {
            "id": c.id,
            "title": getattr(c, "name", ""),
            "artist": cand_artist,
            "album": cand_album,
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
                "title_similarity": self.title_similarity,
                "duration_delta_seconds": self.duration_delta,
            },
        }


def evaluate_candidate(
    original: Track,
    candidate: Track,
    original_upc: Optional[str] = None,
    candidate_upc: Optional[str] = None,
) -> CandidateEvaluation:
    """Evaluate candidate against original track, producing similarity score and reasoning."""
    reasoning_parts = []
    points = 0.0

    # 1. ISRC matching (40 points)
    orig_isrc = (getattr(original, "isrc", None) or "").strip().upper()
    cand_isrc = (getattr(candidate, "isrc", None) or "").strip().upper()
    isrc_match = bool(orig_isrc and cand_isrc and orig_isrc == cand_isrc)

    if isrc_match:
        points += 40.0
        reasoning_parts.append(f"Exact ISRC match ({orig_isrc}).")
    elif orig_isrc and cand_isrc:
        reasoning_parts.append(f"ISRC differs (orig: {orig_isrc}, cand: {cand_isrc}).")
    else:
        reasoning_parts.append("ISRC not available on both tracks.")

    # 2. Album UPC matching (20 points)
    o_upc = (original_upc or getattr(getattr(original, "album", None), "upc", "") or "").strip()
    c_upc = (candidate_upc or getattr(getattr(candidate, "album", None), "upc", "") or "").strip()
    upc_match = bool(o_upc and c_upc and o_upc == c_upc)

    if upc_match:
        points += 20.0
        reasoning_parts.append(f"Identical album UPC barcode ({o_upc}).")
    elif o_upc and c_upc:
        reasoning_parts.append(f"Album UPC differs (orig: {o_upc}, cand: {c_upc}).")

    # 3. Title similarity (15 points)
    orig_title = getattr(original, "name", "") or ""
    cand_title = getattr(candidate, "name", "") or ""
    title_sim = compute_string_similarity(orig_title, cand_title)
    points += title_sim * 15.0

    if title_sim >= 0.95:
        reasoning_parts.append("Title is an exact normalized match.")
    elif title_sim >= 0.8:
        reasoning_parts.append(f"Title is very similar ({round(title_sim*100)}% match).")
    else:
        reasoning_parts.append(f"Title match is weak ({round(title_sim*100)}% match).")

    # 4. Artist matching (10 points)
    orig_art_name = getattr(getattr(original, "artist", None), "name", "") or ""
    cand_art_name = getattr(getattr(candidate, "artist", None), "name", "") or ""
    orig_art_id = getattr(getattr(original, "artist", None), "id", None)
    cand_art_id = getattr(getattr(candidate, "artist", None), "id", None)

    # Immediate rejection of tribute, karaoke, or generic cover artists
    cand_art_lower = cand_art_name.lower()
    if any(k in cand_art_lower for k in ("tribute", "karaoke", "cover band", "instrumental band")):
        reasoning_parts.append(f"[DISQUALIFIED] Tribute / Karaoke artist detected ('{cand_art_name}').")
        return CandidateEvaluation(
            candidate_track=candidate,
            score=0.05,
            reasoning=" ".join(reasoning_parts),
            isrc_match=isrc_match,
            upc_match=upc_match,
            title_similarity=title_sim,
            duration_delta=abs((getattr(original, "duration", 0) or 0) - (getattr(candidate, "duration", 0) or 0)),
            requires_manual_approval=True,
            disqualified=True,
        )

    if orig_art_id and cand_art_id and orig_art_id == cand_art_id:
        points += 10.0
        reasoning_parts.append(f"Exact primary artist match (ID: {orig_art_id}).")
    elif normalize_title(orig_art_name) == normalize_title(cand_art_name):
        points += 9.0
        reasoning_parts.append(f"Artist names match ('{orig_art_name}').")
    else:
        art_sim = compute_string_similarity(orig_art_name, cand_art_name)
        if (orig_art_id and cand_art_id and orig_art_id != cand_art_id and art_sim < 0.6) or art_sim < 0.5:
            reasoning_parts.append(f"[DISQUALIFIED] Artist mismatch: '{cand_art_name}' vs '{orig_art_name}'.")
            return CandidateEvaluation(
                candidate_track=candidate,
                score=0.05,
                reasoning=" ".join(reasoning_parts),
                isrc_match=isrc_match,
                upc_match=upc_match,
                title_similarity=title_sim,
                duration_delta=abs((getattr(original, "duration", 0) or 0) - (getattr(candidate, "duration", 0) or 0)),
                requires_manual_approval=True,
                disqualified=True,
            )
        points += art_sim * 6.0
        reasoning_parts.append(f"Artist names differ slightly ('{cand_art_name}' vs '{orig_art_name}').")

    # 5. Duration delta (10 points)
    orig_dur = getattr(original, "duration", 0) or 0
    cand_dur = getattr(candidate, "duration", 0) or 0
    duration_delta = abs(orig_dur - cand_dur)

    if duration_delta <= 1:
        points += 10.0
        reasoning_parts.append(f"Duration is identical (diff: {duration_delta}s).")
    elif duration_delta <= 3:
        points += 8.0
        reasoning_parts.append(f"Duration is virtually identical (diff: {duration_delta}s).")
    elif duration_delta <= 7:
        points += 4.0
        reasoning_parts.append(f"Duration is close (diff: {duration_delta}s).")
    elif duration_delta <= 15:
        points += 1.0
        reasoning_parts.append(f"Duration differs moderately (diff: {duration_delta}s).")
    else:
        reasoning_parts.append(f"Duration differs significantly (diff: {duration_delta}s).")

    # 6. Album name match (5 points)
    orig_alb_name = getattr(getattr(original, "album", None), "name", "") or ""
    cand_alb_name = getattr(getattr(candidate, "album", None), "name", "") or ""
    alb_sim = compute_string_similarity(orig_alb_name, cand_alb_name)
    points += alb_sim * 5.0

    if alb_sim >= 0.9:
        reasoning_parts.append(f"Same album release ('{cand_alb_name}').")
    elif alb_sim >= 0.5:
        reasoning_parts.append(f"Similar album title ('{cand_alb_name}' vs '{orig_alb_name}').")
    else:
        reasoning_parts.append(f"Different album ('{cand_alb_name}' vs '{orig_alb_name}').")

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
        requires_manual_approval=requires_manual_approval,
        disqualified=False,
    )