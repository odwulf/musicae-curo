"""Tests for PlaylistService operations."""

import json
from unittest.mock import MagicMock
import pytest

from curo.providers.tidal import TidalProvider
from curo.services.playlist_service import PlaylistService


@pytest.fixture
def mock_session():
    s = MagicMock()
    s.user = MagicMock()
    return s


@pytest.fixture
def mock_provider(mock_session):
    return TidalProvider(session=mock_session)


def test_list_playlists(mock_session, mock_provider):
    pl1 = MagicMock()
    pl1.id = "pl-1"
    pl1.name = "Favorites"
    pl1.description = "My favorite tracks"
    pl1.num_tracks = 42
    pl1.duration = 7200
    pl1.public = False
    pl1.last_updated = "2026-10-18T10:00:00Z"

    mock_session.user.playlists.return_value = [pl1]

    svc = PlaylistService(mock_provider)
    playlists = svc.list_playlists()

    assert len(playlists) == 1
    assert playlists[0]["id"] == "pl-1"
    assert playlists[0]["title"] == "Favorites"
    assert playlists[0]["num_tracks"] == 42


def test_get_playlist_by_id_and_name(mock_session, mock_provider):
    pl1 = MagicMock(id="pl-1")
    pl1.name = "Rock Classics"
    pl2 = MagicMock(id="pl-2")
    pl2.name = "Jazz Vibes"

    mock_session.playlist.side_effect = lambda pid: pl1 if pid == "pl-1" else None
    mock_session.user.playlists.return_value = [pl1, pl2]

    svc = PlaylistService(mock_provider)

    # Lookup by ID
    found_by_id = svc.get_playlist("pl-1")
    assert found_by_id is not None
    assert found_by_id.title == "Rock Classics"

    # Lookup by name (case-insensitive)
    found_by_name = svc.get_playlist("jazz vibes")
    assert found_by_name is not None
    assert found_by_name.title == "Jazz Vibes"


def test_add_tracks_chunking(mock_session, mock_provider):
    pl = MagicMock(id="target-pl")
    pl.name = "My Batch Playlist"
    pl.add.return_value = ["1", "2"]
    mock_session.playlist.return_value = pl

    svc = PlaylistService(mock_provider)
    track_ids = [f"t-{i}" for i in range(125)]

    added = svc.add_tracks(playlist="target-pl", track_ids=track_ids)
    assert added is True
    # 125 tracks with chunk_size 50 => 3 calls (50, 50, 25)
    assert pl.add.call_count == 3


def test_export_playlist_json_and_m3u(mock_session, mock_provider):
    pl = MagicMock(id="pl-exp")
    pl.name = "Chillhop"
    pl.description = "Lofi beats"
    t1 = MagicMock(id=101, duration=180, isrc="US1234567890")
    t1.name = "Midnight Chill"
    t1.artist.name = "Lofi Producer"
    t1.album.name = "Study Beats"

    pl.tracks.return_value = [t1]
    mock_session.playlist.return_value = pl

    svc = PlaylistService(mock_provider)

    # JSON export
    json_out = svc.export_playlist("pl-exp", export_format="json")
    parsed = json.loads(json_out)
    assert parsed["name"] == "Chillhop"
    assert parsed["track_count"] == 1
    assert parsed["tracks"][0]["title"] == "Midnight Chill"

    # M3U export
    m3u_out = svc.export_playlist("pl-exp", export_format="m3u8")
    assert "#EXTM3U" in m3u_out
    assert "Midnight Chill" in m3u_out
    assert "track://101" in m3u_out
