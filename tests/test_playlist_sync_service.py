"""Comprehensive unit and integration tests for PlaylistSyncService and cross-provider sync."""

import json
from pathlib import Path
from unittest.mock import MagicMock
import pytest

from curo.config import ConfigManager
from curo.core.models import Album, Artist, Playlist, Track
from curo.core.provider import MusicProvider
from curo.services.job_runner import JobRunner
from curo.services.playlist_sync_service import PlaylistSyncService


def _create_mock_track(
    track_id: str,
    title: str,
    artist_name: str,
    duration: int = 200,
    isrc: str = "USSM18200001",
    upc: str = "012345678901",
    available: bool = True,
) -> Track:
    return Track(
        id=track_id,
        title=title,
        provider="mock",
        duration_seconds=duration,
        artist=Artist(id="art-1", name=artist_name, provider="mock"),
        album=Album(id="alb-1", title="Mock Album", provider="mock", upc=upc),
        isrc=isrc,
        available=available,
        allow_streaming=available,
        stream_ready=available,
    )


def test_resolve_track_by_isrc_exact_match(tmp_path: Path):
    src_provider = MagicMock(spec=MusicProvider)
    src_provider.name = "source_prov"
    src_provider.display_name = "SourceProv"

    tgt_provider = MagicMock(spec=MusicProvider)
    tgt_provider.name = "target_prov"
    tgt_provider.display_name = "TargetProv"

    src_track = _create_mock_track("s1", "Bohemian Rhapsody", "Queen", isrc="GBUM71029604")
    tgt_track = _create_mock_track("t1", "Bohemian Rhapsody", "Queen", isrc="GBUM71029604")

    tgt_provider.get_tracks_by_isrc.return_value = [tgt_track]

    cfg = ConfigManager(custom_config_dir=tmp_path)
    svc = PlaylistSyncService(source_provider=src_provider, target_provider=tgt_provider, cfg=cfg, project_root=tmp_path)

    cand = svc.resolve_track_on_target(src_track, threshold=0.8)
    assert cand is not None
    assert str(cand.candidate.id) == "t1"
    assert cand.isrc_match is True
    assert cand.score >= 0.90


def test_resolve_track_by_upc_barcode(tmp_path: Path):
    src_provider = MagicMock(spec=MusicProvider)
    src_provider.name = "source_prov"

    tgt_provider = MagicMock(spec=MusicProvider)
    tgt_provider.name = "target_prov"
    tgt_provider.get_tracks_by_isrc.return_value = []

    src_track = _create_mock_track("s2", "Under Pressure", "Queen", isrc=None, upc="0077774640428")

    tgt_alb = Album(id="alb-tgt", title="Hot Space", provider="target_prov", upc="0077774640428")
    tgt_track = _create_mock_track("t2", "Under Pressure", "Queen", isrc="GBUM78100010", upc="0077774640428")

    tgt_provider.get_albums_by_barcode.return_value = [tgt_alb]
    tgt_provider.get_album_tracks.return_value = [tgt_track]

    cfg = ConfigManager(custom_config_dir=tmp_path)
    svc = PlaylistSyncService(source_provider=src_provider, target_provider=tgt_provider, cfg=cfg, project_root=tmp_path)

    cand = svc.resolve_track_on_target(src_track, threshold=0.8)
    assert cand is not None
    assert str(cand.candidate.id) == "t2"
    assert cand.upc_match is True


def test_resolve_track_by_fuzzy_metadata_search(tmp_path: Path):
    src_provider = MagicMock(spec=MusicProvider)
    src_provider.name = "source_prov"

    tgt_provider = MagicMock(spec=MusicProvider)
    tgt_provider.name = "target_prov"
    tgt_provider.get_tracks_by_isrc.return_value = []
    tgt_provider.get_albums_by_barcode.return_value = []

    src_track = _create_mock_track("s3", "Raspberry Beret", "Prince", isrc=None, upc=None, duration=211)
    tgt_track = _create_mock_track("t3", "Raspberry Beret", "Prince", isrc=None, upc=None, duration=211)

    tgt_provider.search_tracks.return_value = [tgt_track]

    cfg = ConfigManager(custom_config_dir=tmp_path)
    svc = PlaylistSyncService(source_provider=src_provider, target_provider=tgt_provider, cfg=cfg, project_root=tmp_path)

    cand = svc.resolve_track_on_target(src_track, threshold=0.8)
    assert cand is not None
    assert str(cand.candidate.id) == "t3"
    assert cand.score >= 0.80


def test_resolve_track_unresolvable(tmp_path: Path):
    src_provider = MagicMock(spec=MusicProvider)
    src_provider.name = "source_prov"

    tgt_provider = MagicMock(spec=MusicProvider)
    tgt_provider.name = "target_prov"
    tgt_provider.get_tracks_by_isrc.return_value = []
    tgt_provider.get_albums_by_barcode.return_value = []
    tgt_provider.search_tracks.return_value = []

    src_track = _create_mock_track("s4", "Extremely Obscure Song", "Unknown Artist", isrc="XYZ123")

    cfg = ConfigManager(custom_config_dir=tmp_path)
    svc = PlaylistSyncService(source_provider=src_provider, target_provider=tgt_provider, cfg=cfg, project_root=tmp_path)

    cand = svc.resolve_track_on_target(src_track, threshold=0.8)
    assert cand is None


def test_compare_playlists_diff_calculation(tmp_path: Path):
    src_provider = MagicMock(spec=MusicProvider)
    src_provider.name = "tidal"
    src_provider.display_name = "Tidal"

    tgt_provider = MagicMock(spec=MusicProvider)
    tgt_provider.name = "spotify"
    tgt_provider.display_name = "Spotify"

    src_pl = Playlist(id="pl-src", title="Favorites", provider="tidal", track_count=3)
    tgt_pl = Playlist(id="pl-tgt", title="Favorites Mirror", provider="spotify", track_count=2)

    src_provider.get_playlist.return_value = src_pl
    tgt_provider.get_playlist.return_value = tgt_pl

    t_in_sync = _create_mock_track("s1", "In Sync Song", "Artist A", isrc="ISRC001")
    t_missing = _create_mock_track("s2", "Missing Song", "Artist B", isrc="ISRC002")
    t_unres = _create_mock_track("s3", "Unresolvable Song", "Artist C", isrc="ISRC003")

    tgt_match = _create_mock_track("t1", "In Sync Song", "Artist A", isrc="ISRC001")
    tgt_extra = _create_mock_track("t4", "Extra Target Song", "Artist D", isrc="ISRC004")

    src_provider.get_playlist_tracks.return_value = [t_in_sync, t_missing, t_unres]
    tgt_provider.get_playlist_tracks.return_value = [tgt_match, tgt_extra]

    # Target catalog resolution for missing song
    tgt_resolved_missing = _create_mock_track("t2", "Missing Song", "Artist B", isrc="ISRC002")
    tgt_provider.get_tracks_by_isrc.side_effect = lambda isrc: [tgt_resolved_missing] if isrc == "ISRC002" else []
    tgt_provider.get_albums_by_barcode.return_value = []
    tgt_provider.search_tracks.return_value = []

    cfg = ConfigManager(custom_config_dir=tmp_path)
    svc = PlaylistSyncService(source_provider=src_provider, target_provider=tgt_provider, cfg=cfg, project_root=tmp_path)

    diff = svc.compare_playlists("pl-src", "pl-tgt", threshold=0.8)
    assert diff["source_tracks_total"] == 3
    assert diff["target_tracks_total"] == 2
    assert diff["in_sync_count"] == 1
    assert diff["missing_in_target_count"] == 1
    assert diff["unresolvable_count"] == 1
    assert diff["extra_in_target_count"] == 1
    assert Path(diff["exported_to"]).exists()


def test_sync_playlists_mirror_mode_dry_run_and_live(tmp_path: Path):
    src_provider = MagicMock(spec=MusicProvider)
    src_provider.name = "source_prov"
    src_provider.display_name = "SourceProv"

    tgt_provider = MagicMock(spec=MusicProvider)
    tgt_provider.name = "target_prov"
    tgt_provider.display_name = "TargetProv"

    src_pl = Playlist(id="pl-src-1", title="My Playlist", provider="source_prov", track_count=2)
    tgt_pl = Playlist(id="pl-tgt-1", title="My Playlist", provider="target_prov", track_count=1)

    src_provider.get_playlist.return_value = src_pl
    tgt_provider.get_playlist.return_value = tgt_pl

    s1 = _create_mock_track("s1", "Song 1", "Artist 1", isrc="ISRC1")
    s2 = _create_mock_track("s2", "Song 2", "Artist 2", isrc="ISRC2")
    src_provider.get_playlist_tracks.return_value = [s1, s2]

    old_target = _create_mock_track("t_old", "Old Song", "Old Artist", isrc="ISRC_OLD")
    tgt_provider.get_playlist_tracks.return_value = [old_target]

    t1 = _create_mock_track("t1", "Song 1", "Artist 1", isrc="ISRC1")
    t2 = _create_mock_track("t2", "Song 2", "Artist 2", isrc="ISRC2")
    tgt_provider.get_tracks_by_isrc.side_effect = lambda isrc: [t1] if isrc == "ISRC1" else ([t2] if isrc == "ISRC2" else [])

    cfg = ConfigManager(custom_config_dir=tmp_path)
    svc = PlaylistSyncService(source_provider=src_provider, target_provider=tgt_provider, cfg=cfg, project_root=tmp_path)

    # 1. Dry-run simulation
    sim_res = svc.sync_playlists("pl-src-1", "pl-tgt-1", mode="mirror", dry_run=True)
    assert sim_res["dry_run"] is True
    assert sim_res["tracks_resolved_count"] == 2
    assert sim_res["tracks_added"] == 2
    assert sim_res["tracks_removed"] == 1
    tgt_provider.clear_playlist.assert_not_called()
    tgt_provider.add_tracks_to_playlist.assert_not_called()

    # 2. Live execution
    live_res = svc.sync_playlists("pl-src-1", "pl-tgt-1", mode="mirror", dry_run=False)
    assert live_res["dry_run"] is False
    assert live_res["tracks_added"] == 2
    tgt_provider.clear_playlist.assert_called_once_with("pl-tgt-1")
    tgt_provider.add_tracks_to_playlist.assert_called_once_with("pl-tgt-1", ["t1", "t2"])


def test_sync_playlists_append_mode(tmp_path: Path):
    src_provider = MagicMock(spec=MusicProvider)
    src_provider.name = "source_prov"

    tgt_provider = MagicMock(spec=MusicProvider)
    tgt_provider.name = "target_prov"

    src_pl = Playlist(id="pl-src-app", title="Append Test", provider="source_prov")
    tgt_pl = Playlist(id="pl-tgt-app", title="Append Test", provider="target_prov")

    src_provider.get_playlist.return_value = src_pl
    tgt_provider.get_playlist.return_value = tgt_pl

    s1 = _create_mock_track("s1", "Song 1", "Artist 1", isrc="ISRC1")
    s2 = _create_mock_track("s2", "Song 2", "Artist 2", isrc="ISRC2")
    src_provider.get_playlist_tracks.return_value = [s1, s2]

    # Target already has s1 (under ID t1)
    t1 = _create_mock_track("t1", "Song 1", "Artist 1", isrc="ISRC1")
    t2 = _create_mock_track("t2", "Song 2", "Artist 2", isrc="ISRC2")
    tgt_provider.get_playlist_tracks.return_value = [t1]
    tgt_provider.get_tracks_by_isrc.side_effect = lambda isrc: [t1] if isrc == "ISRC1" else ([t2] if isrc == "ISRC2" else [])

    cfg = ConfigManager(custom_config_dir=tmp_path)
    svc = PlaylistSyncService(source_provider=src_provider, target_provider=tgt_provider, cfg=cfg, project_root=tmp_path)

    res = svc.sync_playlists("pl-src-app", "pl-tgt-app", mode="append", dry_run=False)
    assert res["mode"] == "append"
    assert res["tracks_added"] == 1
    assert res["tracks_removed"] == 0
    tgt_provider.clear_playlist.assert_not_called()
    tgt_provider.add_tracks_to_playlist.assert_called_once_with("pl-tgt-app", ["t2"])


def test_sync_playlists_create_if_missing(tmp_path: Path):
    src_provider = MagicMock(spec=MusicProvider)
    src_provider.name = "source_prov"

    tgt_provider = MagicMock(spec=MusicProvider)
    tgt_provider.name = "target_prov"

    src_pl = Playlist(id="pl-src-new", title="Brand New Playlist", provider="source_prov")
    src_provider.get_playlist.return_value = src_pl
    tgt_provider.get_playlist.return_value = None  # Missing on target

    s1 = _create_mock_track("s1", "Song 1", "Artist 1", isrc="ISRC1")
    src_provider.get_playlist_tracks.return_value = [s1]

    created_tgt_pl = Playlist(id="pl-tgt-created", title="Brand New Playlist", provider="target_prov")
    tgt_provider.create_playlist.return_value = created_tgt_pl

    t1 = _create_mock_track("t1", "Song 1", "Artist 1", isrc="ISRC1")
    tgt_provider.get_tracks_by_isrc.return_value = [t1]

    cfg = ConfigManager(custom_config_dir=tmp_path)
    svc = PlaylistSyncService(source_provider=src_provider, target_provider=tgt_provider, cfg=cfg, project_root=tmp_path)

    res = svc.sync_playlists("pl-src-new", mode="mirror", dry_run=False, create_if_missing=True)
    assert res["target_playlist_created"] is True
    tgt_provider.create_playlist.assert_called_once()
    tgt_provider.add_tracks_to_playlist.assert_called_once_with("pl-tgt-created", ["t1"])


def test_cli_playlist_compare_and_sync_commands(tmp_path: Path, monkeypatch):
    from typer.testing import CliRunner
    from curo.cli import app, state

    temp_cfg = ConfigManager(custom_config_dir=tmp_path)
    monkeypatch.setitem(state, "config", temp_cfg)

    provider = MagicMock(spec=MusicProvider)
    provider.name = "tidal"
    provider.display_name = "Tidal"

    pl1 = Playlist(id="pl-cli-1", title="Master Playlist", provider="tidal", track_count=1)
    pl2 = Playlist(id="pl-cli-2", title="Clone Playlist", provider="tidal", track_count=0)

    provider.get_playlist.side_effect = lambda ident: pl1 if ident == "Master Playlist" or ident == "pl-cli-1" else (pl2 if ident == "Clone Playlist" or ident == "pl-cli-2" else None)

    track1 = _create_mock_track("t1", "Starman", "David Bowie", isrc="GBAYE7200097")
    provider.get_playlist_tracks.side_effect = lambda ident: [track1] if str(ident) == "pl-cli-1" else []
    provider.get_track.return_value = track1
    provider.get_tracks_by_isrc.return_value = [track1]

    monkeypatch.setattr("curo.cli.get_active_provider", lambda *args, **kwargs: provider)

    runner = CliRunner()

    # 1. Compare CLI command
    res_comp = runner.invoke(app, ["playlist", "compare", "Master Playlist", "Clone Playlist"])
    assert res_comp.exit_code == 0
    assert "Playlist Comparison: 'Master Playlist'" in res_comp.stdout
    assert "Starman" in res_comp.stdout
    assert "to_add" in res_comp.stdout

    # 2. Sync CLI command (dry run)
    res_sync = runner.invoke(app, ["playlist", "sync", "Master Playlist", "--target", "Clone Playlist"])
    assert res_sync.exit_code == 0
    assert "Playlist Sync: 'Master Playlist'" in res_sync.stdout
    assert "DRY-RUN (Simulated)" in res_sync.stdout


def test_job_runner_sync_playlist_action(tmp_path: Path, monkeypatch):
    cfg = ConfigManager(custom_config_dir=tmp_path)
    provider = MagicMock(spec=MusicProvider)
    provider.name = "tidal"
    provider.display_name = "Tidal"

    pl1 = Playlist(id="pl-job-1", title="Source PL", provider="tidal")
    pl2 = Playlist(id="pl-job-2", title="Target PL", provider="tidal")

    provider.get_playlist.side_effect = lambda ident: pl1 if ident == "Source PL" else pl2
    t1 = _create_mock_track("t1", "Job Track", "Artist", isrc="ISRC_JOB")
    provider.get_playlist_tracks.side_effect = lambda ident: [t1] if str(ident) == "pl-job-1" else []
    provider.get_track.return_value = t1
    provider.get_tracks_by_isrc.return_value = [t1]

    runner = JobRunner(provider=provider, cfg=cfg)

    # Mock job definition in jobs.yaml
    jobs_yaml = """
jobs:
  test-sync-job:
    description: "Sync playlist"
    actions:
      - action: sync_playlist
        params:
          source_playlist: "Source PL"
          target_playlist: "Target PL"
          mode: "mirror"
          dry_run: true
"""
    cfg.jobs_file.write_text(jobs_yaml, encoding="utf-8")

    res = runner.run_job("test-sync-job")
    assert res["success"] is True
    assert res["actions_executed"] == 1
    assert res["results"][0]["action"] == "sync_playlist"
    assert res["results"][0]["result"]["tracks_resolved_count"] == 1

