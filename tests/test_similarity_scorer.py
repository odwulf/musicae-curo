"""Tests for similarity_scorer multi-factor scoring and version mismatch penalties."""

from unittest.mock import MagicMock
from tidal_cli.services.similarity_scorer import evaluate_candidate


def make_track(name: str, artist_name: str, duration: int, isrc: str = "", album_name: str = "", upc: str = "", artist_id: int = 1):
    t = MagicMock()
    t.id = 100
    t.name = name
    t.duration = duration
    t.isrc = isrc
    art = MagicMock()
    art.id = artist_id
    art.name = artist_name
    t.artist = art
    alb = MagicMock()
    alb.name = album_name
    alb.upc = upc
    t.album = alb
    return t


def test_perfect_isrc_and_upc_match():
    orig = make_track("Billie Jean", "Michael Jackson", 294, isrc="USSM18200001", album_name="Thriller", upc="07464381122")
    cand = make_track("Billie Jean", "Michael Jackson", 294, isrc="USSM18200001", album_name="Thriller", upc="07464381122")

    ev = evaluate_candidate(orig, cand, original_upc="07464381122", candidate_upc="07464381122")
    assert ev.score == 1.0
    assert ev.isrc_match is True
    assert ev.upc_match is True
    assert ev.requires_manual_approval is False
    assert "Exact ISRC match" in ev.reasoning
    assert "Identical album UPC barcode" in ev.reasoning


def test_isrc_match_different_album():
    orig = make_track("Billie Jean", "Michael Jackson", 294, isrc="USSM18200001", album_name="Thriller", upc="07464381122")
    cand = make_track("Billie Jean", "Michael Jackson", 294, isrc="USSM18200001", album_name="Greatest Hits", upc="88697123456")

    ev = evaluate_candidate(orig, cand, original_upc="07464381122", candidate_upc="88697123456")
    assert 0.75 <= ev.score < 1.0
    assert ev.isrc_match is True
    assert ev.upc_match is False
    assert ev.requires_manual_approval is False


def test_severe_live_penalty_requires_manual_approval():
    orig = make_track("Hotel California", "Eagles", 390, isrc="USEE17600001", album_name="Hotel California")
    # Live recording candidate
    cand = make_track("Hotel California (Live at The Forum)", "Eagles", 415, isrc="USEE18000002", album_name="Live 1980")

    ev = evaluate_candidate(orig, cand)
    # Must be heavily penalized (score <= 0.35)
    assert ev.score <= 0.35
    assert ev.requires_manual_approval is True
    assert "[HEAVILY PENALIZED]" in ev.reasoning
    assert "Live" in ev.reasoning


def test_severe_acoustic_penalty_requires_manual_approval():
    orig = make_track("Everlong", "Foo Fighters", 250, isrc="USRC19700001", album_name="The Colour and the Shape")
    cand = make_track("Everlong (Acoustic)", "Foo Fighters", 252, isrc="USRC10900002", album_name="Greatest Hits")

    ev = evaluate_candidate(orig, cand)
    assert ev.score <= 0.40
    assert ev.requires_manual_approval is True
    assert "[HEAVILY PENALIZED]" in ev.reasoning
    assert "Acoustic" in ev.reasoning


def test_different_artist_disqualified():
    orig = make_track("Yesterday", "The Beatles", 125, isrc="GBAYE6500001", artist_id=10)
    cand = make_track("Yesterday", "Tribute Band Stars", 125, isrc="XXYYZ9900001", artist_id=99)

    ev = evaluate_candidate(orig, cand)
    assert ev.score <= 0.10
    assert ev.disqualified is True
    assert "[DISQUALIFIED]" in ev.reasoning

