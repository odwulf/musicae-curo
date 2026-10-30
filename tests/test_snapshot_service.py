"""Tests for SnapshotService, track registry history, and availability alerting."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from curo.cli import app, state
from curo.config import ConfigManager
from curo.providers.tidal import TidalProvider
from curo.services.candidate_finder import CandidateFinder
from curo.services.job_runner import JobRunner
from curo.services.snapshot_service import SnapshotService


@pytest.fixture
def temp_config(tmp_path: Path) -> ConfigManager:
    cfg = ConfigManager(custom_config_dir=tmp_path)
    return cfg


def test_track_registry_and_historical_recovery(temp_config: ConfigManager):
    service = SnapshotService(provider=None, cfg=temp_config)

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

    provider = TidalProvider(session=session, cfg=temp_config)
    service = SnapshotService(provider=provider, cfg=temp_config)

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

    provider = TidalProvider(session=session, cfg=temp_config)
    service = SnapshotService(provider=provider, cfg=temp_config)
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
    assert str(alerts[0]["track_id"]) == "50"
    assert alerts[0]["track_title"] == "Active Song"
    assert alerts[0]["playlist_name"] == "Daily Favorites"


def test_candidate_finder_recovers_historical_isrc(temp_config: ConfigManager):
    session = MagicMock()
    provider = TidalProvider(session=session, cfg=temp_config)
    snapshot_svc = SnapshotService(provider=provider, cfg=temp_config)

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

    finder = CandidateFinder(provider, snapshot_service=snapshot_svc)
    candidates = finder.find_candidates(unavail_track)

    assert len(candidates) >= 1
    best = candidates[0]
    assert str(best.candidate.id) == "301"
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

    provider = TidalProvider(session=session, cfg=temp_config)
    monkeypatch.setattr("curo.services.job_runner.ProviderRegistry.create_provider", lambda *args, **kwargs: provider)

    runner = JobRunner(provider=provider, cfg=temp_config)
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

    provider = TidalProvider(session=session, cfg=temp_config)
    monkeypatch.setattr("curo.cli.get_active_provider", lambda *args, **kwargs: provider)
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


def test_compute_track_diff_detects_relinked_tracks():
    from curo.services.snapshot_service import compute_track_diff

    # Previous snapshot: 3 tracks
    prev_tracks = [
        # Track 1: Will be replaced by ISRC
        {"id": "101", "title": "Track One", "artist": {"name": "Artist A"}, "isrc": "US1111111111", "duration": 200},
        # Track 2: Will be replaced by Metadata (remaster title variation, no ISRC)
        {"id": "102", "title": "Track Two", "artist": {"name": "Artist B"}, "isrc": None, "duration": 180},
        # Track 3: Genuinely removed
        {"id": "103", "title": "Track Three", "artist": {"name": "Artist C"}, "isrc": "US3333333333", "duration": 220},
    ]

    # Current tracks:
    curr_tracks = [
        # Track 1 replacement: New ID, same ISRC
        MagicMock(id=201, title="Track One", name="Track One", isrc="US1111111111", duration=200, artist=MagicMock(name="Artist A")),
        # Track 2 replacement: New ID, remastered title, same artist, duration close (+2s)
        MagicMock(id=202, title="Track Two (2024 Remaster)", name="Track Two (2024 Remaster)", isrc=None, duration=182, artist=MagicMock(name="Artist B")),
        # Track 4: Genuinely added new song
        MagicMock(id=204, title="Brand New Song", name="Brand New Song", isrc="US4444444444", duration=240, artist=MagicMock(name="Artist D")),
    ]
    curr_tracks[0].artist.name = "Artist A"
    curr_tracks[1].artist.name = "Artist B"
    curr_tracks[2].artist.name = "Artist D"

    diff = compute_track_diff(prev_tracks, curr_tracks)

    # Relinked tracks should have 2 items (Track One and Track Two)
    assert len(diff["relinked_tracks"]) == 2
    relinked_ids = [(r["old_id"], r["new_id"]) for r in diff["relinked_tracks"]]
    assert ("101", "201") in relinked_ids
    assert ("102", "202") in relinked_ids

    # Genuinely added should only contain Brand New Song
    assert len(diff["added_tracks"]) == 1
    assert diff["added_tracks"][0]["id"] == "204"
    assert diff["added_tracks"][0]["title"] == "Brand New Song"

    # Genuinely removed should only contain Track Three
    assert len(diff["removed_tracks"]) == 1
    assert diff["removed_tracks"][0]["id"] == "103"
    assert diff["removed_tracks"][0]["title"] == "Track Three"


def test_snapshot_playlist_integration_with_relinking(temp_config: ConfigManager):
    session = MagicMock()
    mock_pl = MagicMock(id="pl-relink")
    mock_pl.name = "Relink Test Playlist"
    mock_pl.description = ""
    mock_pl.public = False

    t1 = MagicMock(id=501, isrc="GB1111111111", duration=200, available=True, allow_streaming=True, stream_ready=True)
    t1.name = "Original Tune"
    t1.artist.name = "Great Band"
    t1.album.name = "Album 1"
    t1.album.upc = "UPC501"
    t1.album.release_date = "2010-01-01"

    mock_pl.tracks.return_value = [t1]
    session.user.playlists.return_value = [mock_pl]
    session.playlist.return_value = mock_pl

    provider = TidalProvider(session=session, cfg=temp_config)
    service = SnapshotService(provider=provider, cfg=temp_config)

    # Initial snapshot v1
    res1 = service.snapshot_playlist("pl-relink")
    assert res1["version"] == 1

    # Tidal replaces track with new ID 502 (same ISRC)
    t1_relinked = MagicMock(id=502, isrc="GB1111111111", duration=200, available=True, allow_streaming=True, stream_ready=True)
    t1_relinked.name = "Original Tune"
    t1_relinked.artist.name = "Great Band"
    t1_relinked.album.name = "Album 1 (Reissue)"
    t1_relinked.album.upc = "UPC502"
    t1_relinked.album.release_date = "2024-01-01"

    mock_pl.tracks.return_value = [t1_relinked]
    res2 = service.snapshot_playlist("pl-relink")
    assert res2["version"] == 2
    assert res2["has_changed"] is True

    diff = res2["track_diff"]
    assert len(diff["relinked_tracks"]) == 1
    assert diff["relinked_tracks"][0]["old_id"] == "501"
    assert diff["relinked_tracks"][0]["new_id"] == "502"
    assert len(diff["added_tracks"]) == 0
    assert len(diff["removed_tracks"]) == 0


def test_snapshot_proactive_replacement_recording(temp_config: ConfigManager):
    mock_provider = MagicMock()
    mock_provider.name = "tidal"
    mock_pl = MagicMock(id="pl-beatles")
    mock_pl.name = "Beatles en francais"
    mock_provider.get_playlist.return_value = mock_pl

    t_healthy = MagicMock(id=26995301, isrc="FRZ016402450", duration=137, available=True, allow_streaming=True, stream_ready=True)
    t_healthy.name = "Je veux prendre ta main"
    t_healthy.artist.name = "Frank Alamo"
    t_healthy.album.name = "Salut Les Copains"
    t_healthy.album.upc = "00602537725496"
    t_healthy.album.release_date = "2014-04-01"

    mock_provider.get_playlist_tracks.return_value = [t_healthy]
    # Proactively discovered replacement
    mock_provider.get_playlist_track_replacements.return_value = {"26995301": "36142925"}

    service = SnapshotService(provider=mock_provider, cfg=temp_config)
    res = service.snapshot_playlist("pl-beatles")
    assert res["status"] == "created"

    hist = service.get_historical_track(26995301)
    assert hist is not None
    assert hist["has_provider_replacement"] is True
    assert hist["provider_replacement_id"] == "36142925"


def test_snapshot_reactive_replacement_alerting(temp_config: ConfigManager):
    mock_provider = MagicMock()
    mock_provider.name = "tidal"
    mock_pl = MagicMock(id="pl-alerts")
    mock_pl.name = "Unavailable Alert Playlist"
    mock_provider.get_playlist.return_value = mock_pl

    t_unavail = MagicMock(id=999999, isrc="FRZ016409999", duration=150, available=False, allow_streaming=False, stream_ready=False)
    t_unavail.name = "Purged Song"
    t_unavail.artist.name = "Some Artist"
    t_unavail.album.name = "Some Album"
    t_unavail.album.upc = "00000000"
    t_unavail.album.release_date = "2010-01-01"

    rep_track = MagicMock(id=888888, isrc="FRZ016409999", duration=150, available=True, allow_streaming=True, stream_ready=True)
    rep_track.name = "Purged Song (Remastered)"
    rep_track.artist.name = "Some Artist"

    mock_provider.get_playlist_tracks.return_value = [t_unavail]
    mock_provider.get_playlist_track_replacements.return_value = {}
    mock_provider.get_track_replacement.return_value = rep_track

    service = SnapshotService(provider=mock_provider, cfg=temp_config)
    res = service.snapshot_playlist("pl-alerts", alert_on_unavailable=True)

    unavail = res["unavailable_tracks"]
    assert len(unavail) == 1
    assert unavail[0]["has_provider_replacement"] is True
    assert unavail[0]["provider_replacement_id"] == "888888"

    recent_alerts = service.get_recent_alerts(limit=5)
    assert len(recent_alerts) >= 1
    alert = recent_alerts[0]
    assert alert["has_provider_replacement"] is True
    assert alert["provider_replacement_id"] == "888888"
    assert "Official replacement available" in alert["message"]


def test_update_followers_metrics(temp_config: ConfigManager):
    mock_provider = MagicMock()
    mock_provider.name = "tidal"

    mock_profile_run1 = MagicMock()
    mock_profile_run1.id = "user123"
    mock_profile_run1.username = "xave@plantefeve.fr"
    mock_profile_run1.display_name = "xave"
    mock_profile_run1.followers_count = 1

    pl1 = MagicMock(id="pl-1", followers_count=5)
    pl1.name = "Beatles en français"

    mock_provider.get_user_profile.return_value = mock_profile_run1
    mock_provider.get_user_playlists.return_value = [pl1]
    mock_provider.get_playlist_followers.return_value = {"pl-1": 5}

    service = SnapshotService(provider=mock_provider, cfg=temp_config)

    # First run: initial baseline
    stats1 = service.update_followers_metrics()
    assert stats1["profile"]["current_followers"] == 1
    assert stats1["profile"]["delta"] == 0
    assert len(stats1["playlist_changes"]) == 0
    assert stats1["has_changes"] is False

    # Second run: profile gained 2 followers (1 -> 3), playlist gained 2 (5 -> 7)
    mock_profile_run2 = MagicMock()
    mock_profile_run2.id = "user123"
    mock_profile_run2.username = "xave@plantefeve.fr"
    mock_profile_run2.display_name = "xave"
    mock_profile_run2.followers_count = 3

    mock_provider.get_user_profile.return_value = mock_profile_run2
    mock_provider.get_playlist_followers.return_value = {"pl-1": 7}

    stats2 = service.update_followers_metrics()
    assert stats2["profile"]["current_followers"] == 3
    assert stats2["profile"]["previous_followers"] == 1
    assert stats2["profile"]["delta"] == 2
    assert len(stats2["playlist_changes"]) == 1
    assert stats2["playlist_changes"][0]["delta"] == 2
    assert stats2["playlist_changes"][0]["current"] == 7
    assert stats2["playlist_changes"][0]["previous"] == 5
    assert stats2["has_changes"] is True


def test_send_daily_digest(temp_config: ConfigManager):
    mock_provider = MagicMock()
    mock_provider.name = "tidal"

    service = SnapshotService(provider=mock_provider, cfg=temp_config)

    # Mock email service and update_followers_metrics
    with patch("curo.services.email_service.EmailService") as mock_email_cls:
        mock_email_instance = MagicMock()
        mock_email_instance.send_change_digest.return_value = {"sent": True, "follower_changes": True}
        mock_email_cls.return_value = mock_email_instance

        # Fake follower stats
        with patch.object(service, "update_followers_metrics", return_value={
            "profile": {"display_name": "xave", "current_followers": 5, "delta": 1},
            "playlist_changes": [],
            "total_playlist_followers": 10,
            "has_changes": True,
        }):
            res = service.send_daily_digest(force=True)

            assert res["sent"] is True
            assert res["follower_changes"] is True
            mock_email_instance.send_change_digest.assert_called_once()
            args, kwargs = mock_email_instance.send_change_digest.call_args
            assert kwargs["follower_stats"]["profile"]["current_followers"] == 5


def test_send_daily_digest_filters_old_snapshots_and_recovers_diff(temp_config: ConfigManager):
    import datetime
    mock_provider = MagicMock()
    mock_provider.name = "tidal"
    mock_provider.display_name = "Tidal"

    service = SnapshotService(provider=mock_provider, cfg=temp_config)
    snaps_dir = service.snapshots_dir

    now_utc = datetime.datetime.now(datetime.timezone.utc)
    today_iso = now_utc.strftime("%Y-%m-%dT%H%M%SZ")
    today_date = now_utc.strftime("%Y-%m-%d")
    yesterday_date = (now_utc - datetime.timedelta(days=1)).strftime("%Y-%m-%d")

    # 1. Old playlist with 1 snapshot from yesterday (should NOT be marked modified)
    p1_dir = snaps_dir / "old_static_playlist"
    p1_dir.mkdir(parents=True)
    (p1_dir / "manifest.json").write_text(json.dumps({
        "playlist_id": "p1",
        "playlist_name": "Old Static Playlist",
        "playlist_slug": "old_static_playlist",
        "snapshots": [
            {"version": 1, "timestamp": f"{yesterday_date}T10:00:00Z", "filename": "v1.json", "track_count": 10, "unavailable_count": 0}
        ]
    }), encoding="utf-8")
    (p1_dir / "v1.json").write_text(json.dumps({
        "snapshot_created_at": f"{yesterday_date}T10:00:00Z",
        "tracks": [{"id": "1", "name": "Song 1", "available": True}],
    }), encoding="utf-8")

    # 2. Brand new playlist created today with 1 snapshot (should be marked modified with track 2 added)
    p2_dir = snaps_dir / "new_today_playlist"
    p2_dir.mkdir(parents=True)
    (p2_dir / "manifest.json").write_text(json.dumps({
        "playlist_id": "p2",
        "playlist_name": "New Today Playlist",
        "playlist_slug": "new_today_playlist",
        "snapshots": [
            {"version": 1, "timestamp": f"{today_date}T08:00:00Z", "filename": "v1.json", "track_count": 1, "unavailable_count": 0}
        ]
    }), encoding="utf-8")
    (p2_dir / "v1.json").write_text(json.dumps({
        "snapshot_created_at": f"{today_date}T08:00:00Z",
        "tracks": [{"id": "2", "name": "Song 2", "artist": {"name": "Artist 2"}, "available": True}],
    }), encoding="utf-8")

    # 3. Existing playlist modified today (v1 from yesterday, v2 from today without saved track_diff)
    p3_dir = snaps_dir / "modified_today_playlist"
    p3_dir.mkdir(parents=True)
    (p3_dir / "manifest.json").write_text(json.dumps({
        "playlist_id": "p3",
        "playlist_name": "Modified Today Playlist",
        "playlist_slug": "modified_today_playlist",
        "snapshots": [
            {"version": 1, "timestamp": f"{yesterday_date}T10:00:00Z", "filename": "v1.json", "track_count": 1, "unavailable_count": 0},
            {"version": 2, "timestamp": f"{today_date}T09:00:00Z", "filename": "v2.json", "track_count": 2, "unavailable_count": 0},
        ]
    }), encoding="utf-8")
    (p3_dir / "v1.json").write_text(json.dumps({
        "snapshot_created_at": f"{yesterday_date}T10:00:00Z",
        "tracks": [{"id": "10", "name": "Original Song", "available": True}],
    }), encoding="utf-8")
    (p3_dir / "v2.json").write_text(json.dumps({
        "snapshot_created_at": f"{today_date}T09:00:00Z",
        "tracks": [
            {"id": "10", "name": "Original Song", "available": True},
            {"id": "20", "name": "Added Song", "available": True},
        ],
    }), encoding="utf-8")

    with patch("curo.services.email_service.EmailService") as mock_email_cls:
        mock_email_instance = MagicMock()
        mock_email_instance.send_change_digest.return_value = {"sent": True}
        mock_email_cls.return_value = mock_email_instance

        with patch.object(service, "update_followers_metrics", return_value={"has_changes": False}):
            res = service.send_daily_digest(force=True)

            mock_email_instance.send_change_digest.assert_called_once()
            _, kwargs = mock_email_instance.send_change_digest.call_args
            modified = kwargs["modified_playlists"]

            # Only p2 and p3 should be modified!
            modified_names = [p["playlist_name"] for p in modified]
            assert "Old Static Playlist" not in modified_names
            assert "New Today Playlist" in modified_names
            assert "Modified Today Playlist" in modified_names

            # Verify track diffs were recovered
            p2_entry = next(p for p in modified if p["playlist_name"] == "New Today Playlist")
            assert len(p2_entry["track_diff"]["added_tracks"]) == 1
            assert p2_entry["track_diff"]["added_tracks"][0]["title"] == "Song 2"

            p3_entry = next(p for p in modified if p["playlist_name"] == "Modified Today Playlist")
            assert len(p3_entry["track_diff"]["added_tracks"]) == 1
            assert p3_entry["track_diff"]["added_tracks"][0]["title"] == "Added Song"


def test_snapshot_deduplicates_unavailable_alerts_and_respects_send_when_no_changes(temp_config: ConfigManager):
    import datetime
    session = MagicMock()
    mock_pl = MagicMock(id="pl-unavail")
    mock_pl.name = "Unavailable Test"
    mock_pl.description = ""
    mock_pl.public = False

    t_unavail = MagicMock(id=999, isrc="US9999999999", duration=180, available=False, allow_streaming=False, stream_ready=False)
    t_unavail.name = "Missing Song"
    t_unavail.artist.name = "Ghost Artist"
    t_unavail.album.name = "Ghost Album"
    t_unavail.album.upc = "UPC999"
    t_unavail.album.release_date = "2020-01-01"

    mock_pl.tracks.return_value = [t_unavail]
    session.user.playlists.return_value = [mock_pl]
    session.playlist.return_value = mock_pl

    provider = TidalProvider(session=session, cfg=temp_config)
    service = SnapshotService(provider=provider, cfg=temp_config)

    # First snapshot: logs 1 new unavailable alert
    res1 = service.snapshot_playlist("pl-unavail", alert_on_unavailable=True)
    assert res1["unavailable_count"] == 1
    assert res1["new_alerts_count"] == 1

    # Second snapshot of the same playlist: should NOT log duplicate alert for same track
    res2 = service.snapshot_playlist("pl-unavail", alert_on_unavailable=True)
    assert res2["unavailable_count"] == 1
    assert res2["new_alerts_count"] == 0
    assert len(service.get_recent_alerts(limit=50)) == 1

    # Simulate alert having been logged yesterday and a duplicate logged today
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    yesterday_iso = (now_utc - datetime.timedelta(days=1)).isoformat()
    today_iso = now_utc.isoformat()
    alert_yesterday = {
        "timestamp": yesterday_iso,
        "playlist_id": "pl-unavail",
        "playlist_name": "Unavailable Test",
        "track_id": 999,
        "title": "Missing Song",
        "artist": "Ghost Artist",
    }
    alert_today_dup = dict(alert_yesterday, timestamp=today_iso)
    service.alerts_log_file.write_text(
        json.dumps(alert_yesterday) + "\n" + json.dumps(alert_today_dup) + "\n",
        encoding="utf-8",
    )

    # Backdate manifest so no playlist is considered modified today
    slug_dir = service.snapshots_dir / "unavailable_test"
    manifest = json.loads((slug_dir / "manifest.json").read_text(encoding="utf-8"))
    for s in manifest["snapshots"]:
        s["timestamp"] = yesterday_iso
        sf = slug_dir / s["filename"]
        sd = json.loads(sf.read_text(encoding="utf-8"))
        sd["snapshot_created_at"] = yesterday_iso
        sf.write_text(json.dumps(sd), encoding="utf-8")
    (slug_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    with patch("curo.services.email_service.EmailService") as mock_email_cls:
        mock_email_instance = MagicMock()
        mock_email_instance.send_change_digest.return_value = {"sent": True}
        mock_email_cls.return_value = mock_email_instance

        with patch.object(service, "update_followers_metrics", return_value={"has_changes": False}):
            # Default (send_when_no_changes=False): should NOT send email
            digest_res = service.send_daily_digest()
            assert digest_res["sent"] is False
            assert digest_res["reason"] == "no_changes_detected"
            mock_email_instance.send_change_digest.assert_not_called()

            # Explicit send_when_no_changes=True: should send email
            digest_forced = service.send_daily_digest(send_when_no_changes=True)
            assert digest_forced["sent"] is True
            mock_email_instance.send_change_digest.assert_called_once()
