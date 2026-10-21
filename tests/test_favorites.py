"""Unit and integration tests for favorite tracks pagination, snapshotting, and playlist mirroring."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from typer.testing import CliRunner

from curo.cli import app, state
from curo.config import ConfigManager
from curo.core.models import Album, Artist, Playlist, Track
from curo.providers.deezer.adapter import DeezerProvider
from curo.providers.spotify.adapter import SpotifyProvider
from curo.providers.tidal.adapter import TidalProvider
from curo.services.job_runner import JobRunner
from curo.services.playlist_service import PlaylistService
from curo.services.snapshot_service import SnapshotService


# ---------------------------------------------------------------------------
# Fixtures & Helpers
# ---------------------------------------------------------------------------
@pytest.fixture
def temp_config(tmp_path: Path) -> ConfigManager:
    return ConfigManager(custom_config_dir=tmp_path)


def _make_mock_tidal_track(track_id: int, title: str, isrc: str = "US1234567890", available: bool = True):
    t = MagicMock()
    t.id = track_id
    t.name = title
    t.title = title
    t.duration = 200
    t.isrc = isrc
    t.available = available
    t.allow_streaming = available
    t.stream_ready = available
    t.artist = MagicMock()
    t.artist.id = 1000 + track_id
    t.artist.name = f"Artist {track_id}"
    t.album = MagicMock()
    t.album.id = 2000 + track_id
    t.album.name = f"Album {track_id}"
    t.album.title = f"Album {track_id}"
    t.album.upc = f"UPC{track_id:06d}"
    t.album.release_date = "2024-01-01"
    t.artists = [t.artist]
    return t


# ---------------------------------------------------------------------------
# Provider Multi-Page Pagination Tests
# ---------------------------------------------------------------------------
def test_tidal_get_favorite_tracks_pagination(temp_config: ConfigManager):
    """Ensure Tidal multi-page loop handles >100 tracks without 50-track truncation."""
    session_mock = MagicMock()
    provider = TidalProvider(session=session_mock, cfg=temp_config)

    # Page 1: 100 tracks; Page 2: 35 tracks (total 135)
    page1 = [_make_mock_tidal_track(i, f"Track {i}") for i in range(1, 101)]
    page2 = [_make_mock_tidal_track(i, f"Track {i}") for i in range(101, 136)]

    def mock_tracks(limit=100, offset=0):
        if offset == 0:
            return page1
        elif offset == 100:
            return page2
        return []

    session_mock.user.favorites.tracks.side_effect = mock_tracks

    tracks = provider.get_favorite_tracks()

    assert len(tracks) == 135
    assert tracks[0].id == "1"
    assert tracks[134].id == "135"
    assert session_mock.user.favorites.tracks.call_count == 3
    session_mock.user.favorites.tracks.assert_any_call(limit=100, offset=0)
    session_mock.user.favorites.tracks.assert_any_call(limit=100, offset=100)
    session_mock.user.favorites.tracks.assert_any_call(limit=100, offset=200)


def test_tidal_get_favorite_tracks_handles_short_page_filtering(temp_config: ConfigManager):
    """Ensure Tidal does not terminate prematurely when a page returns fewer than limit tracks due to filtering."""
    session_mock = MagicMock()
    provider = TidalProvider(session=session_mock, cfg=temp_config)

    session_mock.user.favorites.get_tracks_count.return_value = 150

    # Page 1 has only 87 tracks returned (13 were filtered/unavailable) despite limit=100
    page1 = [_make_mock_tidal_track(i, f"Track {i}") for i in range(1, 88)]
    # Page 2 has 40 tracks
    page2 = [_make_mock_tidal_track(i, f"Track {i}") for i in range(88, 128)]

    def mock_tracks(limit=100, offset=0):
        if offset == 0:
            return page1
        elif offset == 100:
            return page2
        return []

    session_mock.user.favorites.tracks.side_effect = mock_tracks

    tracks = provider.get_favorite_tracks()

    assert len(tracks) == 127
    assert tracks[0].id == "1"
    assert tracks[-1].id == "127"
    session_mock.user.favorites.tracks.assert_any_call(limit=100, offset=0)
    session_mock.user.favorites.tracks.assert_any_call(limit=100, offset=100)


def test_spotify_get_favorite_tracks_pagination(temp_config: ConfigManager):
    """Ensure Spotify multi-page loop traverses 'next' links across pages."""
    session_mock = MagicMock()
    provider = SpotifyProvider(session=session_mock, cfg=temp_config)

    def _make_spotify_item(i: int):
        return {
            "track": {
                "id": f"sp_track_{i}",
                "name": f"Spotify Track {i}",
                "duration_ms": 180000,
                "artists": [{"id": f"sp_art_{i}", "name": f"Spotify Artist {i}"}],
                "album": {"id": f"sp_alb_{i}", "name": f"Spotify Album {i}"},
                "external_ids": {"isrc": f"US-SP-{i:04d}"},
                "is_playable": True,
            }
        }

    page1_items = [_make_spotify_item(i) for i in range(1, 51)]
    page2_items = [_make_spotify_item(i) for i in range(51, 81)]

    page1 = {
        "items": page1_items,
        "next": "https://api.spotify.com/v1/me/tracks?offset=50&limit=50",
    }
    page2 = {
        "items": page2_items,
        "next": None,
    }

    session_mock.current_user_saved_tracks.return_value = page1
    session_mock.next.side_effect = lambda p: page2 if p == page1 else None

    tracks = provider.get_favorite_tracks()

    assert len(tracks) == 80
    assert tracks[0].id == "sp_track_1"
    assert tracks[79].id == "sp_track_80"
    session_mock.current_user_saved_tracks.assert_called_once_with(limit=50)
    session_mock.next.assert_called_once_with(page1)


def test_deezer_get_favorite_tracks_pagination(temp_config: ConfigManager):
    """Ensure Deezer provider iterates over paginated tracks."""
    session_mock = MagicMock()
    provider = DeezerProvider(session=session_mock, cfg=temp_config)

    user_mock = MagicMock()
    session_mock.get_user.return_value = user_mock

    raw_tracks = []
    for i in range(1, 65):
        t = MagicMock()
        t.id = i
        t.title = f"Deezer Track {i}"
        t.duration = 210
        t.isrc = f"FR-DZ-{i:04d}"
        t.readable = True
        t.artist = MagicMock(id=i * 10, name=f"Deezer Artist {i}")
        t.album = MagicMock(id=i * 100, title=f"Deezer Album {i}")
        raw_tracks.append(t)

    user_mock.get_tracks.return_value = iter(raw_tracks)

    tracks = provider.get_favorite_tracks()

    assert len(tracks) == 64
    assert tracks[0].id == "1"
    assert tracks[63].id == "64"
    assert tracks[0].title == "Deezer Track 1"


# ---------------------------------------------------------------------------
# Snapshot Service Favorites Tests
# ---------------------------------------------------------------------------
def test_snapshot_favorite_tracks_versioning_and_alerts(temp_config: ConfigManager):
    mock_provider = MagicMock()
    mock_provider.name = "tidal"
    mock_provider.display_name = "TIDAL"

    t1 = Track(
        id="101",
        title="Healthy Song",
        provider="tidal",
        duration_seconds=180,
        artist=Artist(id="1", name="Artist One"),
        album=Album(id="1", title="Album One", upc="UPC001"),
        isrc="US1111111111",
        available=True,
    )
    t2 = Track(
        id="102",
        title="Unavailable Song",
        provider="tidal",
        duration_seconds=200,
        artist=Artist(id="2", name="Artist Two"),
        album=Album(id="2", title="Album Two", upc="UPC002"),
        isrc="US2222222222",
        available=False,
    )

    mock_provider.get_favorite_tracks.return_value = [t1, t2]

    svc = SnapshotService(provider=mock_provider, cfg=temp_config)

    # 1. First snapshot (baseline: no previous snapshot, so added/removed diff is empty)
    snap1 = svc.snapshot_favorite_tracks(alert_on_unavailable=True)

    assert snap1["success"] is True
    assert snap1["track_count"] == 2
    assert snap1["version"] == 1
    assert snap1["unavailable_alerts_count"] == 1
    assert len(snap1["added_tracks"]) == 0
    assert len(snap1["removed_tracks"]) == 0

    # Verify track registry saved healthy track
    registry_file = temp_config.track_registry_file
    assert registry_file.exists()
    reg_data = json.loads(registry_file.read_text(encoding="utf-8"))
    assert "101" in reg_data
    assert reg_data["101"]["isrc"] == "US1111111111"

    # Verify alert was written
    alerts = svc.get_recent_alerts(limit=10)
    assert len(alerts) == 1
    assert alerts[0]["title"] == "Unavailable Song"

    # 2. Second snapshot with track 1 removed, track 3 added
    t3 = Track(
        id="103",
        title="New Song",
        provider="tidal",
        duration_seconds=220,
        artist=Artist(id="3", name="Artist Three"),
        album=Album(id="3", title="Album Three"),
        isrc="US3333333333",
        available=True,
    )
    mock_provider.get_favorite_tracks.return_value = [t2, t3]

    snap2 = svc.snapshot_favorite_tracks(alert_on_unavailable=False)
    assert snap2["version"] == 2
    assert len(snap2["added_tracks"]) == 1
    assert snap2["added_tracks"][0]["id"] == "103"
    assert len(snap2["removed_tracks"]) == 1
    assert str(snap2["removed_tracks"][0]["id"]) == "101"


# ---------------------------------------------------------------------------
# Playlist Mirroring Tests
# ---------------------------------------------------------------------------
def test_backup_favorites_to_playlist_replace_and_append(temp_config: ConfigManager):
    mock_provider = MagicMock()
    mock_provider.name = "tidal"
    mock_provider.display_name = "TIDAL"

    t1 = Track(id="1", title="Track 1", provider="tidal", duration_seconds=180, artist=Artist(id="a1", name="Artist 1"))
    t2 = Track(id="2", title="Track 2", provider="tidal", duration_seconds=190, artist=Artist(id="a2", name="Artist 2"))
    mock_provider.get_favorite_tracks.return_value = [t1, t2]

    # Target playlist
    target_pl = Playlist(
        id="pl_favs",
        title="Favorites Backup",
        provider="tidal",
        track_count=0,
    )
    mock_provider.get_playlist.return_value = target_pl
    mock_provider.add_tracks_to_playlist.return_value = True
    mock_provider.clear_playlist.return_value = True

    svc = PlaylistService(provider=mock_provider)

    # 1. Replace mode
    res_replace = svc.backup_favorites_to_playlist(target_playlist="Favorites Backup", mode="replace")
    assert res_replace["success"] is True
    assert res_replace["total_favorites"] == 2
    assert res_replace["tracks_added_count"] == 2
    mock_provider.clear_playlist.assert_called_once_with("pl_favs")
    mock_provider.add_tracks_to_playlist.assert_called_with("pl_favs", ["1", "2"], position=None)

    # 2. Append mode
    mock_provider.clear_playlist.reset_mock()
    mock_provider.add_tracks_to_playlist.reset_mock()
    # Assume track 1 is already in target playlist
    mock_provider.get_playlist_tracks.return_value = [t1]

    res_append = svc.backup_favorites_to_playlist(target_playlist="Favorites Backup", mode="append")
    assert res_append["success"] is True
    assert res_append["tracks_added_count"] == 1  # only track 2 added
    mock_provider.clear_playlist.assert_not_called()
    mock_provider.add_tracks_to_playlist.assert_called_with("pl_favs", ["2"], position=None)


def test_export_favorites(temp_config: ConfigManager):
    mock_provider = MagicMock()
    mock_provider.name = "spotify"
    t1 = Track(
        id="sp1",
        title="Bohemian Rhapsody",
        provider="spotify",
        duration_seconds=354,
        artist=Artist(id="a1", name="Queen"),
        album=Album(id="alb1", title="A Night at the Opera"),
        isrc="GBUM71029604",
    )
    mock_provider.get_favorite_tracks.return_value = [t1]

    svc = PlaylistService(provider=mock_provider)

    # JSON export
    json_out = svc.export_favorites(export_format="json")
    data = json.loads(json_out)
    assert data["track_count"] == 1
    assert data["tracks"][0]["title"] == "Bohemian Rhapsody"

    # M3U8 export
    m3u_out = svc.export_favorites(export_format="m3u8")
    assert "#EXTM3U" in m3u_out
    assert "Queen - Bohemian Rhapsody" in m3u_out

    # CSV export
    csv_out = svc.export_favorites(export_format="csv")
    assert "Queen" in csv_out
    assert "GBUM71029604" in csv_out


# ---------------------------------------------------------------------------
# Job Runner Integration
# ---------------------------------------------------------------------------
def test_job_runner_daily_favorites_backup(temp_config: ConfigManager):
    mock_provider = MagicMock()
    mock_provider.name = "tidal"
    mock_provider.display_name = "TIDAL"

    t1 = Track(
        id="1",
        title="Track 1",
        provider="tidal",
        duration_seconds=180,
        isrc="US1",
        artist=Artist(id="a1", name="Artist 1"),
    )
    mock_provider.get_favorite_tracks.return_value = [t1]
    mock_provider.get_playlist.return_value = Playlist(id="pl_1", title="Favorites Backup", provider="tidal")
    mock_provider.clear_playlist.return_value = True
    mock_provider.add_tracks_to_playlist.return_value = True

    temp_config.ensure_jobs_file()
    runner = JobRunner(provider=mock_provider, cfg=temp_config)

    res = runner.run_job("daily-favorites-backup")
    assert res["success"] is True
    assert res["actions_executed"] == 2


# ---------------------------------------------------------------------------
# CLI Commands Tests
# ---------------------------------------------------------------------------
def test_cli_favorites_commands(temp_config: ConfigManager, monkeypatch):
    runner = CliRunner()
    monkeypatch.setitem(state, "config", temp_config)

    mock_provider = MagicMock()
    mock_provider.name = "tidal"
    mock_provider.display_name = "TIDAL"

    t1 = Track(
        id="101",
        title="Midnight City",
        provider="tidal",
        duration_seconds=243,
        artist=Artist(id="a1", name="M83"),
        album=Album(id="alb1", title="Hurry Up, We're Dreaming"),
        isrc="FR6V81100010",
        available=True,
    )
    mock_provider.get_favorite_tracks.return_value = [t1]
    mock_provider.get_playlist.return_value = Playlist(id="fav_pl", title="Favorites Backup", provider="tidal")
    mock_provider.clear_playlist.return_value = True
    mock_provider.add_tracks_to_playlist.return_value = True

    with patch("curo.cli.get_active_provider", return_value=mock_provider):
        # 1. curo favorites list
        res_list = runner.invoke(app, ["favorites", "list"])
        assert res_list.exit_code == 0
        assert "Midnight" in res_list.stdout
        assert "M83" in res_list.stdout

        # 2. curo favorites snapshot
        res_snap = runner.invoke(app, ["favorites", "snapshot"])
        assert res_snap.exit_code == 0
        assert "Favorites Snapshot" in res_snap.stdout

        # 3. curo favorites backup
        res_backup = runner.invoke(app, ["favorites", "backup"])
        assert res_backup.exit_code == 0
        assert "Favorites Backup Complete" in res_backup.stdout

        # 4. curo favorites export --format csv
        res_export = runner.invoke(app, ["favorites", "export", "-f", "csv"])
        assert res_export.exit_code == 0
        assert "Midnight City" in res_export.stdout
        assert "FR6V81100010" in res_export.stdout