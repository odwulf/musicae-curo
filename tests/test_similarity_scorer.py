"""Tests for similarity_scorer multi-factor scoring and version mismatch penalties."""

from unittest.mock import MagicMock
from curo.services.similarity_scorer import evaluate_candidate


def make_track(name: str, artist_name: str, duration: int, isrc: str = "", album_name: str = "", upc: str = "", artist_id: int = 1, release_date: str = "", artists: list = None):
    t = MagicMock()
    t.id = 100
    t.name = name
    t.duration = duration
    t.isrc = isrc
    art = MagicMock()
    art.id = artist_id
    art.name = artist_name
    t.artist = art
    if artists is not None:
        t.artists = artists
    else:
        t.artists = [art]
    alb = MagicMock()
    alb.name = album_name
    alb.upc = upc
    alb.release_date = release_date
    t.album = alb
    return t


def test_perfect_metadata_without_isrc_scores_80_percent():
    """Verify real-world retired Tidal tracks with missing ISRC/UPC score 80% on exact metadata."""
    orig = make_track("Losing My Religion", "Applewood Road", 275, isrc="", album_name="Applewood Road", upc="")
    cand = make_track("Losing My Religion", "Applewood Road", 275, isrc="GBYG51531015", album_name="Applewood Road", upc="")

    ev = evaluate_candidate(orig, cand, original_upc="", candidate_upc="")
    # Exact title (30) + artist (25) + duration (15) + album (10) = 80 points
    assert ev.score == 0.8
    assert ev.isrc_match is False
    assert ev.upc_match is False
    assert ev.requires_manual_approval is False
    assert "Title is an exact normalized match (+30 pts)" in ev.reasoning
    assert "Exact primary artist match" in ev.reasoning
    assert "Same album release" in ev.reasoning


def test_perfect_isrc_and_upc_match_scores_100_percent():
    orig = make_track("Billie Jean", "Michael Jackson", 294, isrc="USSM18200001", album_name="Thriller", upc="07464381122")
    cand = make_track("Billie Jean", "Michael Jackson", 294, isrc="USSM18200001", album_name="Thriller", upc="07464381122")

    ev = evaluate_candidate(orig, cand, original_upc="07464381122", candidate_upc="07464381122")
    assert ev.score == 1.0
    assert ev.isrc_match is True
    assert ev.upc_match is True
    assert ev.requires_manual_approval is False
    assert "Exact ISRC match confirmed" in ev.reasoning
    assert "Identical album UPC barcode confirmed" in ev.reasoning


def test_isrc_match_with_different_album_scores_90_percent():
    orig = make_track("Billie Jean", "Michael Jackson", 294, isrc="USSM18200001", album_name="Thriller", upc="07464381122")
    # Same ISRC and title/artist/duration, but different album and upc
    cand = make_track("Billie Jean", "Michael Jackson", 294, isrc="USSM18200001", album_name="Thriller (25th Anniversary)", upc="88697123456")

    ev = evaluate_candidate(orig, cand, original_upc="07464381122", candidate_upc="88697123456")
    # Title (30) + Artist (25) + Duration (15) + Similar Album (4-10) + ISRC (10) >= 0.80
    assert 0.80 <= ev.score < 1.0
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


def test_release_date_bonus_exact_match():
    """Exact release date adds 5% bonus to similarity score."""
    orig = make_track("Clint Eastwood", "Gorillaz", 340, album_name="Gorillaz", release_date="2001-03-26")
    cand = make_track("Clint Eastwood", "Gorillaz", 340, album_name="Gorillaz", release_date="2001-03-26")

    ev = evaluate_candidate(orig, cand)
    # Base match (80) + Exact release date (+5) = 85 points
    assert ev.score == 0.85
    assert ev.release_date_match is True
    assert "Identical album release date confirmed (2001-03-26, +5 pts)" in ev.reasoning


def test_release_date_bonus_same_year():
    """Matching release year without exact day adds 2% bonus."""
    orig = make_track("Clint Eastwood", "Gorillaz", 340, album_name="Gorillaz", release_date="2001-03-26")
    cand = make_track("Clint Eastwood", "Gorillaz", 340, album_name="Gorillaz", release_date="2001-11-05")

    ev = evaluate_candidate(orig, cand)
    # Base match (80) + Same year (+2) = 82 points
    assert ev.score == 0.82
    assert ev.release_date_match is False
    assert "Same album release year (2001, +2 pts)" in ev.reasoning


def test_release_date_different_year_no_bonus():
    """Different release year yields no release date bonus."""
    orig = make_track("Clint Eastwood", "Gorillaz", 340, album_name="Gorillaz", release_date="2001-03-26")
    cand = make_track("Clint Eastwood", "Gorillaz", 340, album_name="Gorillaz", release_date="2017-04-28")

    ev = evaluate_candidate(orig, cand)
    # Base match (80) + 0 bonus = 80 points
    assert ev.score == 0.80
    assert ev.release_date_match is False
    assert "Album release date differs" in ev.reasoning


def test_collaborating_artists_inverted_billing_matches_full_points():
    """Duets/collaborations with flipped primary artist credits should match full artist points."""
    art_nick = MagicMock(id=9003, name="Nick Cave & The Bad Seeds")
    art_kylie = MagicMock(id=2573, name="Kylie Minogue")

    orig = make_track(
        name="Where the Wild Roses Grow (2011 Remaster)",
        artist_name="Nick Cave & The Bad Seeds",
        duration=237,
        album_name="Murder Ballads (2011 Remastered Version)",
        artist_id=9003,
        release_date="1996-02-01",
        artists=[art_nick, art_kylie],
    )
    cand = make_track(
        name="Where the Wild Roses Grow",
        artist_name="Kylie Minogue",
        duration=237,
        album_name="Murder Ballads (Remastered 2011)",
        artist_id=2573,
        release_date="1996-02-01",
        artists=[art_kylie, art_nick],
    )

    ev = evaluate_candidate(orig, cand)
    # Title (30) + Collaborating Artists (25) + Duration (15) + Album (10) + Release Date (5) = 85
    assert ev.score == 0.85
    assert ev.release_date_match is True
    assert "Exact collaborating artists match" in ev.reasoning
    assert "Title is an exact normalized match" in ev.reasoning
    assert "Same album release" in ev.reasoning



def test_official_provider_replacement_bonus_and_high_score():
    orig = make_track(
        name="Je veux prendre ta main",
        artist_name="Frank Alamo",
        duration=137,
        album_name="Salut Les Copains",
        artist_id=3545740,
    )
    cand = make_track(
        name="Je veux prendre ta main",
        artist_name="Frank Alamo",
        duration=136,
        album_name="Tendres Annees",
        artist_id=3545740,
    )
    ev = evaluate_candidate(orig, cand, is_provider_replacement=True)
    assert ev.score >= 0.95
    assert ev.is_provider_replacement is True
    assert "Official provider replacement" in ev.reasoning
    d = ev.to_dict()
    assert d["is_provider_replacement"] is True
    assert d["breakdown"]["is_provider_replacement"] is True
