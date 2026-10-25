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

