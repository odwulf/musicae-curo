"""Tests for PlaylistRepairService diagnostic reports and dry-run safety."""

import json
from pathlib import Path
from unittest.mock import MagicMock
from tidal_cli.services.playlist_repair_service import PlaylistRepairService


def test_analyze_and_repair_generates_json_report(tmp_path: Path):
    session = MagicMock()

    # Unavailable original track
    unavail_track = MagicMock(
        id=999,
        isrc="USSM18200001",
        duration=294,
        available=False,
        allow_streaming=False,
        stream_ready=False,
    )
    unavail_track.name = "Billie Jean"
    unavail_track.artist.name = "Michael Jackson"
    unavail_track.album.name = "Thriller"
    unavail_track.album.upc = "07464381122"

    # Playable track in same playlist
    ok_track = MagicMock(
        id=888,
        isrc="USSM18200002",
        duration=250,
        available=True,
        allow_streaming=True,
        stream_ready=True,
    )
    ok_track.name = "Beat It"
    ok_track.artist.name = "Michael Jackson"
    ok_track.album.name = "Thriller"

    # Playlist
    pl = MagicMock(id="pl-test-123")
    pl.name = "80s Pop Favorites"
    pl.tracks.return_value = [unavail_track, ok_track]
    session.playlist.return_value = pl

    # Candidate substitute
    cand = MagicMock(
        id=1001,
        isrc="USSM18200001",
        duration=294,
        available=True,
        allow_streaming=True,
        stream_ready=True,
    )
    cand.name = "Billie Jean"
    cand.artist.name = "Michael Jackson"
    cand.album.name = "Thriller"
    cand.album.upc = "07464381122"

    session.get_tracks_by_isrc.return_value = [cand]
    session.get_albums_by_barcode.return_value = []
    session.search.return_value = {"tracks": []}

    svc = PlaylistRepairService(session, project_root=tmp_path)
    report = svc.analyze_and_repair(
        identifier="pl-test-123",
        threshold=1.0,  # 100% threshold baseline
        dry_run=True,   # Dry-run baseline
    )

    assert report["total_tracks"] == 2
    assert report["unavailable_tracks_count"] == 1
    assert report["replacements_applied"] == 0
    assert report["dry_run"] is True

    unavail_entry = report["unavailable_tracks"][0]
    assert unavail_entry["original_track"]["title"] == "Billie Jean"
    assert unavail_entry["best_candidate"]["id"] == 1001
    assert "Exact ISRC match" in unavail_entry["best_candidate"]["reasoning"]

    # Verify report was written into reports/ subfolder
    report_file = Path(report["exported_to"])
    assert report_file.exists()
    assert "reports" in str(report_file)

    saved_data = json.loads(report_file.read_text(encoding="utf-8"))
    assert saved_data["playlist_name"] == "80s Pop Favorites"
    assert saved_data["unavailable_tracks_count"] == 1


def test_live_replacement_calls_add_with_allow_duplicates_and_removes(tmp_path: Path):
    from tidalapi.playlist import UserPlaylist

    session = MagicMock()
    unavail_track = MagicMock(id=999, isrc="USSM18200001", duration=294, available=False, allow_streaming=False, stream_ready=False)
    unavail_track.name = "Billie Jean"
    unavail_track.artist.name = "Michael Jackson"
    unavail_track.album.name = "Thriller"
    unavail_track.album.upc = "07464381122"

    pl = MagicMock(spec=UserPlaylist)
    pl.id = "pl-test-123"
    pl.name = "Live Test"
    pl.tracks.return_value = [unavail_track]
    pl.add.return_value = [1001]
    pl.remove_by_index.return_value = True
    session.playlist.return_value = pl

    cand = MagicMock(id=1001, isrc="USSM18200001", duration=294, available=True, allow_streaming=True, stream_ready=True)
    cand.name = "Billie Jean"
    cand.artist.name = "Michael Jackson"
    cand.album.name = "Thriller"
    cand.album.upc = "07464381122"

    session.get_tracks_by_isrc.return_value = [cand]
    session.get_albums_by_barcode.return_value = []
    session.search.return_value = {"tracks": []}

    svc = PlaylistRepairService(session, project_root=tmp_path)
    report = svc.analyze_and_repair(
        identifier="pl-test-123",
        threshold=0.8,
        dry_run=False,
    )

    assert report["replacements_applied"] == 1
    pl.add.assert_called_once_with(["1001"], position=0, allow_duplicates=True)
    pl.remove_by_index.assert_called_once_with(1)


def test_live_replacement_safeguard_when_tidal_add_skipped(tmp_path: Path):
    from tidalapi.playlist import UserPlaylist

    session = MagicMock()
    unavail_track = MagicMock(id=999, isrc="USSM18200001", duration=294, available=False, allow_streaming=False, stream_ready=False)
    unavail_track.name = "Billie Jean"
    unavail_track.artist.name = "Michael Jackson"
    unavail_track.album.name = "Thriller"
    unavail_track.album.upc = "07464381122"

    pl = MagicMock(spec=UserPlaylist)
    pl.id = "pl-test-123"
    pl.name = "Live Test"
    pl.tracks.return_value = [unavail_track]
    # Tidal returns empty list when skipped
    pl.add.return_value = []
    session.playlist.return_value = pl

    cand = MagicMock(id=1001, isrc="USSM18200001", duration=294, available=True, allow_streaming=True, stream_ready=True)
    cand.name = "Billie Jean"
    cand.artist.name = "Michael Jackson"
    cand.album.name = "Thriller"
    cand.album.upc = "07464381122"

    session.get_tracks_by_isrc.return_value = [cand]
    session.get_albums_by_barcode.return_value = []
    session.search.return_value = {"tracks": []}

    svc = PlaylistRepairService(session, project_root=tmp_path)
    report = svc.analyze_and_repair(
        identifier="pl-test-123",
        threshold=0.8,
        dry_run=False,
    )

    assert report["replacements_applied"] == 0
    pl.add.assert_called_once_with(["1001"], position=0, allow_duplicates=True)
    # MUST NOT remove when add failed!
    pl.remove_by_index.assert_not_called()
    assert report["unavailable_tracks"][0]["replacement_status"] == "error: tidal_skipped_add"


def test_analyze_and_repair_default_threshold_is_80_percent(tmp_path: Path):
    session = MagicMock()
    pl = MagicMock()
    pl.id = "pl-123"
    pl.name = "Defaults Test"
    pl.tracks.return_value = []
    session.playlist.return_value = pl

    svc = PlaylistRepairService(session, project_root=tmp_path)
    report = svc.analyze_and_repair(identifier="pl-123")

    assert report["threshold_configured"] == 0.8
    assert report["dry_run"] is True


def test_resolve_replacement_track_by_id_and_isrc(tmp_path: Path):
    session = MagicMock()
    orig = MagicMock(id=101, duration=200)
    orig.name = "Song A"
    orig.artist.name = "Artist A"
    orig.album.name = "Album A"

    # Replacement by ID
    cand_by_id = MagicMock(id=202, duration=200, available=True, allow_streaming=True, stream_ready=True)
    cand_by_id.name = "Song A"
    cand_by_id.artist.name = "Artist A"
    cand_by_id.album.name = "Album A (Remaster)"
    session.track.return_value = cand_by_id

    svc = PlaylistRepairService(session, project_root=tmp_path)
    t, method, eval_res = svc.resolve_replacement_track(orig, replacement="202")
    assert t.id == 202
    assert "track_id" in method
    assert eval_res.score >= 0.75

    # Replacement by ISRC
    cand_by_isrc = MagicMock(id=303, isrc="USSM18200001", duration=200, available=True, allow_streaming=True, stream_ready=True)
    cand_by_isrc.name = "Song A"
    cand_by_isrc.artist.name = "Artist A"
    cand_by_isrc.album.name = "Album A"
    session.get_tracks_by_isrc.return_value = [cand_by_isrc]

    t2, method2, eval_res2 = svc.resolve_replacement_track(orig, isrc="USSM18200001")
    assert t2.id == 303
    assert "isrc" in method2
    assert eval_res2.isrc_match is True

    # Replacement by auto-detected ISRC in replacement argument
    t3, method3, eval_res3 = svc.resolve_replacement_track(orig, replacement="USSM18200001")
    assert t3.id == 303
    assert "isrc" in method3


def test_resolve_replacement_track_by_upc(tmp_path: Path):
    session = MagicMock()
    orig = MagicMock(id=101, duration=180)
    orig.name = "Song B"
    orig.artist.name = "Artist B"
    orig.album.name = "Album B"

    alb = MagicMock(upc="012345678901")
    alb_track = MagicMock(id=404, duration=180, available=True, allow_streaming=True, stream_ready=True)
    alb_track.name = "Song B"
    alb_track.artist.name = "Artist B"
    alb_track.album.name = "Album B"
    alb_track.album.upc = "012345678901"
    alb.tracks.return_value = [alb_track]

    session.get_albums_by_barcode.return_value = [alb]

    svc = PlaylistRepairService(session, project_root=tmp_path)
    t, method, eval_res = svc.resolve_replacement_track(orig, upc="012345678901")
    assert t.id == 404
    assert "upc" in method
    assert eval_res.upc_match is True


def test_replace_track_dry_run_and_live(tmp_path: Path):
    from tidalapi.playlist import UserPlaylist

    session = MagicMock()
    pl = MagicMock(spec=UserPlaylist)
    pl.id = "pl-replace-1"
    pl.name = "Replace Test Playlist"
    pl.add.return_value = True
    pl.remove_by_index.return_value = True

    t1 = MagicMock(id=501, duration=210)
    t1.name = "Old Track 1"
    t1.artist.name = "Artist 1"
    t1.album.name = "Album 1"

    t2 = MagicMock(id=502, duration=220)
    t2.name = "Old Track 2"
    t2.artist.name = "Artist 2"
    t2.album.name = "Album 2"

    pl.tracks.return_value = [t1, t2]
    session.playlist.return_value = pl

    cand = MagicMock(id=999, duration=220, available=True, allow_streaming=True, stream_ready=True)
    cand.name = "Old Track 2"
    cand.artist.name = "Artist 2"
    cand.album.name = "Album 2 (Reissue)"
    session.track.return_value = cand

    svc = PlaylistRepairService(session, project_root=tmp_path)

    # 1. Dry run simulation by track ID
    sim_res = svc.replace_track(
        playlist_identifier="pl-replace-1",
        target=502,
        replacement=999,
        dry_run=True,
    )
    assert sim_res["status"] == "simulated"
    assert sim_res["position"] == 2
    assert sim_res["replacement_track"]["id"] == 999
    pl.add.assert_not_called()
    pl.remove_by_index.assert_not_called()

    # 2. Live replacement by 1-based index "#2"
    live_res = svc.replace_track(
        playlist_identifier="pl-replace-1",
        target="#2",
        replacement=999,
        dry_run=False,
    )
    assert live_res["status"] == "replaced"
    pl.add.assert_called_once_with(["999"], position=1, allow_duplicates=True)
    pl.remove_by_index.assert_called_once_with(2)


def test_cli_playlist_replace_command(tmp_path: Path, monkeypatch):
    from typer.testing import CliRunner
    from tidal_cli.cli import app, state
    from tidal_cli.config import ConfigManager
    from tidalapi.playlist import UserPlaylist

    temp_cfg = ConfigManager(custom_config_dir=tmp_path)
    monkeypatch.setitem(state, "config", temp_cfg)

    session = MagicMock()
    pl = MagicMock(spec=UserPlaylist)
    pl.id = "pl-cli-replace"
    pl.name = "CLI Replace Playlist"
    pl.add.return_value = True
    pl.remove_by_index.return_value = True

    t1 = MagicMock(id=111, duration=180)
    t1.name = "Song 1"
    t1.artist.name = "Band 1"
    t1.album.name = "Album 1"
    pl.tracks.return_value = [t1]
    session.playlist.return_value = pl

    cand = MagicMock(id=222, duration=180, available=True, allow_streaming=True, stream_ready=True)
    cand.name = "Song 1"
    cand.artist.name = "Band 1"
    cand.album.name = "Album 1 (2020)"
    session.track.return_value = cand

    monkeypatch.setattr("tidal_cli.cli.SessionManager.load_session", lambda self: session)

    cli_runner = CliRunner()

    # Dry run
    res_dry = cli_runner.invoke(
        app,
        ["--json", "playlist", "replace", "pl-cli-replace", "--track", "111", "--with", "222"],
    )
    assert res_dry.exit_code == 0
    dry_data = json.loads(res_dry.stdout)
    assert dry_data["status"] == "simulated"
    assert dry_data["dry_run"] is True

    # Live run
    res_live = cli_runner.invoke(
        app,
        ["playlist", "replace", "pl-cli-replace", "--track", "#1", "--with", "222", "--live"],
    )
    assert res_live.exit_code == 0
    assert "Successfully replaced track #1" in res_live.stdout




