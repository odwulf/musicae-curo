"""Tests for metadata_serializer extracting full playlist, album, and track metadata."""

import datetime
import json
from unittest.mock import MagicMock
from tidal_cli.services.metadata_serializer import (
    serialize_album,
    serialize_playlist,
    serialize_track,
)


def make_full_mock_track():
    t = MagicMock()
    t.id = 123456
    t.name = "Bohemian Rhapsody"
    t.full_name = "Bohemian Rhapsody (2011 Remaster)"
    t.duration = 354
    t.track_num = 11
    t.volume_num = 1
    t.index = 10
    t.isrc = "GBUM71029604"
    t.explicit = False
    t.popularity = 95
    t.audio_quality = "LOSSLESS"
    t.audio_modes = ["STEREO", "DOLBY_ATMOS"]
    t.is_lossless = True
    t.is_hi_res_lossless = False
    t.is_dolby_atmos = True
    t.bpm = 72
    t.key = "Bb"
    t.key_scale = "MAJOR"
    t.replay_gain = -9.2
    t.peak = 0.99
    t.copyright = "(C) 2011 Queen Productions Ltd."
    t.allow_streaming = True
    t.stream_ready = True
    t.stream_start_date = datetime.date(2011, 1, 1)
    t.tidal_release_date = datetime.datetime(2011, 1, 1, 0, 0)
    t.version = "2011 Remaster"
    t.listen_url = "https://tidal.com/browse/track/123456"

    # Artist
    artist = MagicMock()
    artist.id = 1001
    artist.name = "Queen"
    t.artist = artist
    t.artists = [artist]

    # Album
    album = MagicMock()
    album.id = 2002
    album.name = "A Night At The Opera"
    album.release_date = datetime.date(1975, 11, 21)
    album.cover = "cover-uuid-1234"
    t.album = album

    return t


def test_serialize_track():
    track = make_full_mock_track()
    data = serialize_track(track)

    assert data["id"] == 123456
    assert data["name"] == "Bohemian Rhapsody"
    assert data["full_name"] == "Bohemian Rhapsody (2011 Remaster)"
    assert data["isrc"] == "GBUM71029604"
    assert data["duration_seconds"] == 354
    assert data["track_number"] == 11
    assert data["audio_quality"] == "LOSSLESS"
    assert data["is_dolby_atmos"] is True
    assert data["bpm"] == 72
    assert data["artist"]["name"] == "Queen"
    assert data["album"]["name"] == "A Night At The Opera"
    assert data["album"]["release_date"] == "1975-11-21"

    # Ensure it dumps to JSON cleanly
    json_str = json.dumps(data)
    assert "GBUM71029604" in json_str


def test_serialize_album():
    alb = MagicMock()
    alb.id = 555
    alb.name = "A Night At The Opera"
    alb.version = "Deluxe Edition"
    alb.duration = 2600
    alb.num_tracks = 1
    alb.num_volumes = 1
    alb.release_date = datetime.date(1975, 11, 21)
    alb.year = 1975
    alb.copyright = "(P) 1975 Queen"
    alb.explicit = False
    alb.upc = "00602527718021"
    alb.audio_quality = "HI_RES_LOSSLESS"
    alb.cover = "album-cover-id"

    art = MagicMock()
    art.id = 1001
    art.name = "Queen"
    alb.artist = art
    alb.artists = [art]

    track = make_full_mock_track()
    data = serialize_album(alb, [track])

    assert data["type"] == "album"
    assert data["id"] == 555
    assert data["name"] == "A Night At The Opera"
    assert data["upc"] == "00602527718021"
    assert data["release_date"] == "1975-11-21"
    assert data["num_tracks"] == 1
    assert len(data["tracks"]) == 1
    assert data["tracks"][0]["name"] == "Bohemian Rhapsody"
    assert data["tracks"][0]["isrc"] == "GBUM71029604"

    # Verify JSON serializability
    json_str = json.dumps(data, indent=2)
    assert "00602527718021" in json_str


def test_serialize_playlist():
    pl = MagicMock()
    pl.id = "pl-rock-77"
    pl.name = "Classic Rock Gems"
    pl.description = "Best rock tracks of all time"
    pl.created = datetime.datetime(2025, 5, 1, 12, 0)
    pl.last_updated = datetime.datetime(2026, 10, 18, 9, 30)
    pl.duration = 354
    pl.public = True
    pl.creator.id = 8888
    pl.creator.name = "rock_fanatic"

    track = make_full_mock_track()
    data = serialize_playlist(pl, [track])

    assert data["type"] == "playlist"
    assert data["id"] == "pl-rock-77"
    assert data["name"] == "Classic Rock Gems"
    assert data["creator"]["name"] == "rock_fanatic"
    assert data["created"] == "2025-05-01T12:00:00"
    assert data["num_tracks"] == 1
    assert data["tracks"][0]["name"] == "Bohemian Rhapsody"

    # Verify JSON serializability
    json_str = json.dumps(data, indent=2)
    assert "Classic Rock Gems" in json_str

