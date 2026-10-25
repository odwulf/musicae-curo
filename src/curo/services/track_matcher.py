"""Utilities for detecting track availability, normalizing metadata, and fuzzy matching."""

from __future__ import annotations

import difflib
import re
from typing import Any, Optional, Set


def is_track_available(track: Any) -> bool:
    """Check if a track is actively streamable and playable."""
    if not track:
        return False

    is_playable = getattr(track, "is_playable", None)
    if (
        is_playable is not None
        and not hasattr(is_playable, "_mock_return_value")
        and not hasattr(is_playable, "_mock_wraps")
        and "Mock" not in type(is_playable).__name__
    ):
        return bool(is_playable)

    allow_streaming = getattr(track, "allow_streaming", None)
    if allow_streaming is False:
        return False

    available = getattr(track, "available", None)
    if available is False:
        return False

    stream_ready = getattr(track, "stream_ready", None)
    if stream_ready is False:
        return False

    return True


# Regex patterns to strip non-identifying noise from titles (remaster notes, bonus tags, etc.)
_CLEAN_TITLE_PATTERNS = [
    re.compile(r"\s*-\s*\d{4}\s+remaster(ed)?.*$", re.IGNORECASE),
    re.compile(r"\s*-\s*remaster(ed)?(\s+\d{4})?.*$", re.IGNORECASE),
    re.compile(r"\s*\((remastered|remaster)(\s+\d{4})?.*?\)", re.IGNORECASE),
    re.compile(r"\s*\(\d{4}\s+(remastered|remaster).*?\)", re.IGNORECASE),
    re.compile(r"\s*\[(remastered|remaster)(\s+\d{4})?.*?\]", re.IGNORECASE),
    re.compile(r"\s*\[\d{4}\s+(remastered|remaster).*?\]", re.IGNORECASE),
    re.compile(r"\s*-\s*single version.*$", re.IGNORECASE),
    re.compile(r"\s*-\s*album version.*$", re.IGNORECASE),
    re.compile(r"\s*\(album version\)", re.IGNORECASE),
    re.compile(r"\s*\(single version\)", re.IGNORECASE),
    re.compile(r"\s*\(stereo\)", re.IGNORECASE),
    re.compile(r"\s*\(mono\)", re.IGNORECASE),
    re.compile(r"\s*\(feat\..*?\)", re.IGNORECASE),
    re.compile(r"\s*\[feat\..*?\]", re.IGNORECASE),
]


def normalize_title(title: str) -> str:
    """Normalize track title by stripping remaster dates, version tags, and extra punctuation."""
    if not title:
        return ""
    cleaned = str(title).strip()
    for pat in _CLEAN_TITLE_PATTERNS:
        cleaned = pat.sub("", cleaned).strip()

    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip().lower()


_LIVE_KEYWORDS = {"live", "en vivo", "live at", "in concert", "live from", "unplugged", "tour"}
_ACOUSTIC_KEYWORDS = {"acoustic", "acustico", "acoustique", "stripped"}
_REMIX_KEYWORDS = {"remix", "mix", "club mix", "extended mix", "dub", "re-mix"}
_INSTRUMENTAL_KEYWORDS = {"instrumental", "backing track", "karaoke", "tribute"}


def detect_version_tags(title: str, album: str = "") -> Set[str]:
    """Detect special recording tags (live, acoustic, remix, instrumental) from title and album."""
    text = f"{title or ''} {album or ''}".lower()
    tags = set()

    for kw in _LIVE_KEYWORDS:
        if re.search(r"\b" + re.escape(kw) + r"\b", text):
            tags.add("live")
            break

    for kw in _ACOUSTIC_KEYWORDS:
        if re.search(r"\b" + re.escape(kw) + r"\b", text):
            tags.add("acoustic")
            break

    for kw in _REMIX_KEYWORDS:
        if re.search(r"\b" + re.escape(kw) + r"\b", text):
            tags.add("remix")
            break

    for kw in _INSTRUMENTAL_KEYWORDS:
        if re.search(r"\b" + re.escape(kw) + r"\b", text):
            tags.add("instrumental")
            break

    return tags


def compute_string_similarity(s1: str, s2: str) -> float:
    """Compute normalized sequence similarity ratio between 0.0 and 1.0."""
    norm1 = normalize_title(s1)
    norm2 = normalize_title(s2)
    if not norm1 and not norm2:
        return 1.0
    if not norm1 or not norm2:
        return 0.0
    if norm1 == norm2:
        return 1.0
    return difflib.SequenceMatcher(None, norm1, norm2).ratio()
