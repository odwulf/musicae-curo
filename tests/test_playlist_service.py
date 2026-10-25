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


def test_add_tracks_strictly_validates_position_not_none(mock_session, mock_provider):
    """Verify that UserPlaylist.add does not receive position=None, which raises TypeError."""
    pl = MagicMock(id="target-pl-strict")
    pl.name = "Strict Playlist"

    def _mock_add(media_ids, allow_duplicates=False, position=-1, limit=100):
        if position is None:
            raise TypeError("'<' not supported between instances of 'NoneType' and 'int'")
        if position < 0:
            position = 10
        return list(media_ids)

    pl.add.side_effect = _mock_add
    mock_session.playlist.return_value = pl

    svc = PlaylistService(mock_provider)
    track_ids = ["t-1", "t-2", "t-3"]

    added = svc.add_tracks(playlist="target-pl-strict", track_ids=track_ids)
    assert added is True
    assert pl.add.call_count == 1
    # Check that position was not passed as None
    call_kwargs = pl.add.call_args[1]
    assert "position" not in call_kwargs or call_kwargs["position"] is not None


def test_add_tracks_412_etag_precondition_retry(mock_session, mock_provider):
    """Verify that HTTP 412 (ETag mismatch) triggers refresh and retry."""
    pl1 = MagicMock(id="target-pl-412")
    pl1.name = "Retry Playlist"

    attempts = {"count": 0}

    def _mock_add_with_412(*args, **kwargs):
        attempts["count"] += 1
        if attempts["count"] == 1:
            err = Exception("412 Client Error: Precondition Failed for url: https://api.tidal.com/v1/playlists/target-pl-412/items")
            raise err
        return ["t-1", "t-2"]

    pl1.add.side_effect = _mock_add_with_412
    mock_session.playlist.return_value = pl1

    svc = PlaylistService(mock_provider)
    added = svc.add_tracks(playlist="target-pl-412", track_ids=["t-1", "t-2"])

    assert added is True
    assert attempts["count"] == 2
    assert pl1.add.call_count == 2


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


def test_get_folders_and_create_in_folder(mock_session, mock_provider):
    # Setup mock request response for folders
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "items": [
            {
                "name": "Backups",
                "data": {"id": "f-123", "name": "Backups", "totalNumberOfItems": 5},
            }
        ],
        "totalNumberOfItems": 1,
    }
    mock_session.request.request.return_value = mock_resp

    # Make session.user not a mock so TidalProvider queries request
    class RealUser:
        def __init__(self):
            self.id = "u-1"
        def create_playlist(self, title, description, parent_id="root"):
            pl = MagicMock()
            pl.id = "pl-new"
            pl.name = title
            pl.parent_id = parent_id
            return pl
        def create_folder(self, title):
            f = MagicMock()
            f.id = f"f-{title}"
            f.name = title
            return f

    real_user = RealUser()
    mock_session.user = real_user

    svc = PlaylistService(mock_provider)

    # 1. Test get_folders
    folders = svc.get_folders()
    assert len(folders) == 1
    assert folders[0]["id"] == "f-123"
    assert folders[0]["name"] == "Backups"

    # 2. Test create_playlist in existing folder
    pl = svc.create_playlist(title="My Backup PL", description="Desc", folder="Backups")
    assert pl.id == "pl-new"

    # 3. Test create_playlist in new folder that needs creation
    pl2 = svc.create_playlist(title="Another PL", description="Desc", folder="New Folder")
    assert pl2.id == "pl-new"


def test_get_or_create_playlist_moves_existing_to_folder(mock_session, mock_provider):
    existing_pl = MagicMock(id="pl-exist")
    existing_pl.name = "Existing Playlist"
    mock_session.playlist.return_value = existing_pl

    mock_folder = MagicMock()
    mock_session.folder.return_value = mock_folder

    mock_provider.get_folders = MagicMock(return_value=[{"id": "f-bk", "name": "Backups"}])

    svc = PlaylistService(mock_provider)
    res = svc.get_or_create_playlist(title="Existing Playlist", folder="Backups")
    assert res.id == "pl-exist"
    mock_folder.add_items.assert_called_once_with(["pl-exist"])
