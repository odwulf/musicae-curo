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
