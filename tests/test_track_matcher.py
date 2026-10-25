"""Tests for track_matcher availability and title normalization."""

from unittest.mock import MagicMock
from curo.services.track_matcher import (
    compute_string_similarity,
    detect_version_tags,
    is_track_available,
    normalize_title,
)


def test_is_track_available():
    t_ok = MagicMock(available=True, allow_streaming=True, stream_ready=True)
    assert is_track_available(t_ok) is True

    t_unavail = MagicMock(available=False, allow_streaming=True, stream_ready=True)
    assert is_track_available(t_unavail) is False

    t_no_stream = MagicMock(available=True, allow_streaming=False, stream_ready=True)
    assert is_track_available(t_no_stream) is False

    t_not_ready = MagicMock(available=True, allow_streaming=True, stream_ready=False)
    assert is_track_available(t_not_ready) is False


def test_normalize_title():
    assert normalize_title("Bohemian Rhapsody - 2011 Remaster") == "bohemian rhapsody"
    assert normalize_title("Where the Wild Roses Grow (2011 Remaster)") == "where the wild roses grow"
    assert normalize_title("Murder Ballads (2011 Remastered Version)") == "murder ballads"
    assert normalize_title("Murder Ballads (Remastered 2011)") == "murder ballads"
    assert normalize_title("Billie Jean (Single Version)") == "billie jean"
    assert normalize_title("Heroes - 1999 Remastered Version") == "heroes"
    assert normalize_title("Get Lucky (feat. Pharrell Williams)") == "get lucky"


def test_detect_version_tags():
    assert "live" in detect_version_tags("Hotel California (Live at The Forum)")
    assert "acoustic" in detect_version_tags("Layla (Acoustic)")
    assert "remix" in detect_version_tags("Levitating (The Blessed Madonna Remix)")
    assert "instrumental" in detect_version_tags("Yesterday (Instrumental Karaoke)")
    assert len(detect_version_tags("Bohemian Rhapsody", "A Night At The Opera")) == 0


def test_compute_string_similarity():
    assert compute_string_similarity("Billie Jean", "Billie Jean - 2008 Remaster") == 1.0
    assert compute_string_similarity("Billie Jean", "Thriller") < 0.5