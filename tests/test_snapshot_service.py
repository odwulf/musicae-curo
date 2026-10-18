"""Tests for SnapshotService, track registry history, and availability alerting."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from tidal_cli.cli import app, state
from tidal_cli.config import ConfigManager
from tidal_cli.services.candidate_finder import CandidateFinder
from tidal_cli.services.job_runner import JobRunner
from tidal_cli.services.snapshot_service import SnapshotService


@pytest.fixture
def temp_config(tmp_path: Path) -> ConfigManager:
    cfg = ConfigManager(custom_config_dir=tmp_path)
    return cfg


def test_track_registry_and_historical_recovery(temp_config: ConfigManager):
    service = SnapshotService(session=None, cfg=temp_config)

    # 1. Available track with full identifiers
    track1 = MagicMock(id=101, isrc="USSM18200001", available=True, allow_streaming=True, stream_ready=True)
    track1.name = "Billie Jean"
    track1.artist.name = "Michael Jackson"
    track1.album.name = "Thriller"
    track1.album.upc = "07464381122"
    track1.album.release_date = "1982-11-30"

    # 2. Unavailable track (should NOT be added to healthy registry)
    track2 = MagicMock(id=102, isrc=None, available=False, allow_streaming=False, stream_ready=False)
    track2.name = "Delisted Song"
    track2.artist.name = "Some Artist"
    track2.album.name = "Some Album"
    track2.album.upc = None
    track2.album.release_date = None

    service.update_track_registry([track1, track2], playlist_slug="test_playlist")

    hist1 = service.get_historical_track(101)
    assert hist1 is not None
    assert hist1["isrc"] == "USSM18200001"
    assert hist1["album_upc"] == "07464381122"
    assert hist1["album_release_date"] == "1982-11-30"

    hist2 = service.get_historical_track(102)
    assert hist2 is None


def test_snapshot_versioning_and_deduplication(temp_config: ConfigManager):
    session = MagicMock()
    mock_pl = MagicMock(id="pl-001")
    mock_pl.name = "Favorites"
    mock_pl.description = "My favorites"
    mock_pl.public = False

    t1 = MagicMock(id=1, isrc="ISRC1", duration=200, available=True, allow_streaming=True, stream_ready=True)
    t1.name = "Song A"
    t1.artist.name = "Artist A"
    t1.album.name = "Album A"
    t1.album.upc = "UPC1"
    t1.album.release_date = "2020-01-01"

    mock_pl.tracks.return_value = [t1]
    session.user.playlists.return_value = [mock_pl]
    session.playlist.return_value = mock_pl

    service = SnapshotService(session=session, cfg=temp_config)

    # First snapshot: should create version 1
    res1 = service.snapshot_playlist("pl-001")
    assert res1["status"] == "created"
    assert res1["has_changed"] is True
    assert res1["version"] == 1
    assert Path(res1["snapshot_file"]).exists()

    # Second snapshot with identical playlist: should detect unchanged
    res2 = service.snapshot_playlist("pl-001")
    assert res2["status"] == "unchanged"
    assert res2["has_changed"] is False
    assert res2["version"] == 1

    # Modify playlist (add track)
    t2 = MagicMock(id=2, isrc="ISRC2", duration=210, available=True, allow_streaming=True, stream_ready=True)
    t2.name = "Song B"
    t2.artist.name = "Artist B"
    t2.album.name = "Album B"
    t2.album.upc = "UPC2"
    t2.album.release_date = "2021-01-01"
    mock_pl.tracks.return_value = [t1, t2]

    res3 = service.snapshot_playlist("pl-001")
    assert res3["status"] == "created"
    assert res3["has_changed"] is True
    assert res3["version"] == 2
    assert res3["track_count"] == 2


def test_availability_alerting_when_track_delisted(temp_config: ConfigManager):
    session = MagicMock()
    mock_pl = MagicMock(id="pl-002")
    mock_pl.name = "Daily Favorites"
    mock_pl.description = ""
    mock_pl.public = False

    # Healthy track
    t1 = MagicMock(id=50, isrc="ISRC50", duration=180, available=True, allow_streaming=True, stream_ready=True)
    t1.name = "Active Song"
    t1.artist.name = "Good Artist"
    t1.album.name = "Good Album"
    t1.album.upc = "UPC50"
    t1.album.release_date = "2022-01-01"

    mock_pl.tracks.return_value = [t1]
    session.user.playlists.return_value = [mock_pl]
    session.playlist.return_value = mock_pl

    service = SnapshotService(session=session, cfg=temp_config)
    res1 = service.snapshot_playlist("pl-002")
    assert res1["new_alerts_count"] == 0

    # Track becomes unavailable in next snapshot run
    t1_delisted = MagicMock(id=50, isrc=None, duration=180, available=False, allow_streaming=False, stream_ready=False)
    t1_delisted.name = "Active Song"
    t1_delisted.artist.name = "Good Artist"
    t1_delisted.album.name = "Good Album"
    t1_delisted.album.upc = None
    t1_delisted.album.release_date = "2022-01-01"

    mock_pl.tracks.return_value = [t1_delisted]
    res2 = service.snapshot_playlist("pl-002")
    assert res2["new_alerts_count"] == 1
    assert res2["unavailable_count"] == 1

    alerts = service.get_recent_alerts(limit=10)
    assert len(alerts) >= 1
    assert alerts[0]["track_id"] == 50
    assert alerts[0]["track_title"] == "Active Song"
    assert alerts[0]["playlist_name"] == "Daily Favorites"


def test_candidate_finder_recovers_historical_isrc(temp_config: ConfigManager):
    session = MagicMock()
    snapshot_svc = SnapshotService(session=session, cfg=temp_config)

    # 1. Register healthy track in historical registry
    healthy_track = MagicMock(id=300, isrc="FRUM71800123", duration=240, available=True, allow_streaming=True, stream_ready=True)
    healthy_track.name = "Formidable"
    healthy_track.artist.name = "Stromae"
    healthy_track.album.name = "Racine Carrée"
    healthy_track.album.upc = "00602537466840"
    healthy_track.album.release_date = "2013-08-16"

    snapshot_svc.update_track_registry([healthy_track], playlist_slug="stromae_best")

    # 2. Unavailable track with no ISRC or UPC (like delisted track on Tidal)
    unavail_track = MagicMock(id=300, isrc=None, duration=240, available=False, allow_streaming=False, stream_ready=False)
    unavail_track.name = "Formidable"
    unavail_track.artist.name = "Stromae"
    unavail_track.album.name = "Racine Carrée"
    unavail_track.album.upc = None
    unavail_track.album.release_date = "2013-08-16"

    # Replacement candidate on Tidal with the recovered ISRC
    active_cand = MagicMock(id=301, isrc="FRUM71800123", duration=240, available=True, allow_streaming=True, stream_ready=True)
    active_cand.name = "Formidable"
    active_cand.artist.name = "Stromae"
    active_cand.album.name = "Racine Carrée"
    active_cand.album.upc = "00602537466840"
    active_cand.album.release_date = "2013-08-16"

    session.get_tracks_by_isrc.return_value = [active_cand]
    session.get_albums_by_barcode.return_value = []
    session.search.return_value = {"tracks": []}

    finder = CandidateFinder(session, snapshot_service=snapshot_svc)
    candidates = finder.find_candidates(unavail_track)

    assert len(candidates) >= 1
    best = candidates[0]
    assert best.candidate.id == 301
    assert best.isrc_match is True
    # With historical ISRC matched, score should be >= 0.90
    assert best.score >= 0.90


def test_job_runner_snapshot_actions(temp_config: ConfigManager, monkeypatch):
    session = MagicMock()
    mock_pl = MagicMock(id="pl-test")
    mock_pl.name = "Test Playlist"
    mock_pl.description = ""
    mock_pl.public = False

    t = MagicMock(id=99, isrc="ISRC99", duration=150, available=True, allow_streaming=True, stream_ready=True)
    t.name = "Track"
    t.artist.name = "Artist"
    t.album.name = "Album"
    t.album.upc = "UPC99"
    t.album.release_date = "2024-01-01"

    mock_pl.tracks.return_value = [t]
    session.user.playlists.return_value = [mock_pl]
    session.playlist.return_value = mock_pl

    # Mock SessionManager.load_session
    monkeypatch.setattr("tidal_cli.services.job_runner.SessionManager.load_session", lambda self, auto_refresh=True: session)

    runner = JobRunner(cfg=temp_config)
    res = runner.run_job("daily-playlist-backup")

    assert res["success"] is True
    assert res["actions_executed"] == 1
    action_res = res["results"][0]["result"]
    assert action_res["playlists_scanned"] == 1
    assert action_res["snapshots_saved"] == 1


def test_cli_playlist_snapshot_and_alerts(temp_config: ConfigManager, monkeypatch):
    session = MagicMock()
    mock_pl = MagicMock(id="pl-123")
    mock_pl.name = "My Jams"
    mock_pl.description = ""
    mock_pl.public = False

    t = MagicMock(id=77, isrc="ISRC77", duration=210, available=True, allow_streaming=True, stream_ready=True)
    t.name = "Jam 1"
    t.artist.name = "Band"
    t.album.name = "Album"
    t.album.upc = "UPC77"
    t.album.release_date = "2023-01-01"

    mock_pl.tracks.return_value = [t]
    session.user.playlists.return_value = [mock_pl]
    session.playlist.return_value = mock_pl

    monkeypatch.setattr("tidal_cli.cli.SessionManager.load_session", lambda self: session)
    monkeypatch.setitem(state, "config", temp_config)

    cli_runner = CliRunner()

    # Test snapshot command
    res_snap = cli_runner.invoke(app, ["--json", "playlist", "snapshot", "pl-123"])
    assert res_snap.exit_code == 0
    data = json.loads(res_snap.stdout)
    assert len(data) == 1
    assert data[0]["name"] == "My Jams"
    assert data[0]["status"] == "created"

    # Test alerts command (empty)
    res_alerts = cli_runner.invoke(app, ["--json", "playlist", "alerts"])
    assert res_alerts.exit_code == 0
    alerts_data = json.loads(res_alerts.stdout)
    assert isinstance(alerts_data, list)
