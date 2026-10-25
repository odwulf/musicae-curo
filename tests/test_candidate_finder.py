"""Tests for candidate_finder tiered search logic."""

from unittest.mock import MagicMock
from curo.providers.tidal import TidalProvider
from curo.services.candidate_finder import CandidateFinder


def test_candidate_finder_isrc_and_search_fallback():
    session = MagicMock()

    # Unavailable original track
    orig = MagicMock(id=101, isrc="USSM18200001", duration=294)
    orig.name = "Billie Jean"
    orig.artist.name = "Michael Jackson"
    orig.album.upc = "07464381122"
    orig.album.name = "Thriller"

    # ISRC replacement track
    isrc_cand = MagicMock(id=202, isrc="USSM18200001", duration=294, available=True, allow_streaming=True, stream_ready=True)
    isrc_cand.name = "Billie Jean"
    isrc_cand.artist.name = "Michael Jackson"
    isrc_cand.album.upc = "07464381122"
    isrc_cand.album.name = "Thriller (Reissue)"

    session.get_tracks_by_isrc.return_value = [isrc_cand]
    session.get_albums_by_barcode.return_value = []
    session.search.return_value = {"tracks": []}

    provider = TidalProvider(session=session)
    finder = CandidateFinder(provider)
    candidates = finder.find_candidates(orig)

    assert len(candidates) >= 1
    best = candidates[0]
    assert str(best.candidate.id) == "202"
    assert best.score >= 0.8
    assert best.isrc_match is True


def test_candidate_finder_tier_0_official_replacement():
    session = MagicMock()

    # Unavailable original track
    orig = MagicMock(id=101, isrc=None, duration=137)
    orig.name = "Je veux prendre ta main"
    orig.artist.name = "Frank Alamo"
    orig.album.upc = None
    orig.album.name = "Salut Les Copains"

    # Official replacement candidate
    rep_track = MagicMock(id=36142925, isrc="FRZ016402450", duration=136, available=True, allow_streaming=True, stream_ready=True)
    rep_track.name = "Je veux prendre ta main"
    rep_track.artist.name = "Frank Alamo"
    rep_track.album.upc = "00044006463827"
    rep_track.album.name = "Tendres Annees"

    provider = MagicMock()
    provider.get_track_replacement.return_value = rep_track
    provider.get_tracks_by_isrc.return_value = []
    provider.get_albums_by_barcode.return_value = []
    provider.search_tracks.return_value = []

    finder = CandidateFinder(provider)
    candidates = finder.find_candidates(orig)

    assert len(candidates) >= 1
    best = candidates[0]
    assert str(best.candidate.id) == "36142925"
    assert best.score >= 0.95
    assert best.is_provider_replacement is True
    assert "Official provider replacement" in best.reasoning


def test_candidate_finder_tier_0_from_historical_cache():
    orig = MagicMock(id=101, isrc=None, duration=137)
    orig.name = "Je veux prendre ta main"
    orig.artist.name = "Frank Alamo"
    orig.album = None

    rep_track = MagicMock(id=36142925, isrc="FRZ016402450", duration=136, available=True, allow_streaming=True, stream_ready=True)
    rep_track.name = "Je veux prendre ta main"
    rep_track.artist.name = "Frank Alamo"
    rep_track.album = None

    snapshot_svc = MagicMock()
    snapshot_svc.get_historical_track.return_value = {
        "isrc": "FRZ016402450",
        "provider_replacement_id": "36142925",
    }

    provider = MagicMock()
    provider.get_track.return_value = rep_track
    provider.get_track_replacement.return_value = None
    provider.get_tracks_by_isrc.return_value = []
    provider.get_albums_by_barcode.return_value = []
    provider.search_tracks.return_value = []

    finder = CandidateFinder(provider, snapshot_service=snapshot_svc)
    candidates = finder.find_candidates(orig)

    assert len(candidates) >= 1
    best = candidates[0]
    assert str(best.candidate.id) == "36142925"
    assert best.is_provider_replacement is True
    assert best.score >= 0.95
