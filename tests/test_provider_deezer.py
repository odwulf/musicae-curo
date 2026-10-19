"""Comprehensive unit tests for DeezerProvider and DeezerSessionManager."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from typer.testing import CliRunner

from curo.cli import app, state
from curo.config import ConfigManager
from curo.core.models import Album, Artist, CuratedMix, Playlist, Track
from curo.core.registry import ProviderRegistry
from curo.providers.deezer.adapter import DeezerProvider
from curo.providers.deezer.session import DeezerAuthenticationError, DeezerSessionManager


@pytest.fixture
def mock_deezer_session():
    mock = MagicMock()
    user_mock = MagicMock()
    user_mock.id = 12345
    user_mock.name = "Deezer Tester"
    user_mock.country = "FR"
    mock.get_user.return_value = user_mock
    return mock


def test_deezer_provider_registration(tmp_path: Path):
    cfg = ConfigManager(custom_config_dir=tmp_path)
    p = ProviderRegistry.create_provider("deezer", cfg=cfg)
    assert isinstance(p, DeezerProvider)
    assert p.name == "deezer"
    assert p.display_name == "Deezer"


def test_deezer_session_manager_credentials_and_status(tmp_path: Path, monkeypatch):
    cfg = ConfigManager(custom_config_dir=tmp_path)
    sm = DeezerSessionManager(cfg)

    # 1. No credentials configured
    assert sm.get_status()["logged_in"] is False

    # 2. Save session data
    sm.save_session_data(
        access_token="tok_12345",
        user_info={"id": 12345, "name": "Deezer Tester", "country": "FR"},
        app_id="app_111",
        app_secret="sec_222",
    )
    tok, app_id, app_sec, r_uri = sm.get_credentials()
    assert tok == "tok_12345"
    assert app_id == "app_111"
    assert app_sec == "sec_222"
    assert r_uri == "http://127.0.0.1:8888/callback"

    # 3. Environment variable override
    monkeypatch.setenv("DEEZER_ACCESS_TOKEN", "env_tok")
    monkeypatch.setenv("DEEZER_APP_ID", "env_app_id")
    monkeypatch.setenv("DEEZER_APP_SECRET", "env_app_sec")
    c_tok, c_app, c_sec, _ = sm.get_credentials()
    assert c_tok == "env_tok"
    assert c_app == "env_app_id"
    assert c_sec == "env_app_sec"

    # 4. Logout removes session file
    assert sm.session_file.exists()
    assert sm.logout() is True
    assert not sm.session_file.exists()

    # 5. Missing token raises DeezerAuthenticationError on load_session
    monkeypatch.delenv("DEEZER_ACCESS_TOKEN", raising=False)
    with pytest.raises(DeezerAuthenticationError, match="No Deezer session found"):
        sm.load_session()


def test_domain_mapping_artist_and_album(tmp_path: Path):
    cfg = ConfigManager(custom_config_dir=tmp_path)
    p = DeezerProvider(cfg=cfg)

    # Artist dict mapping
    art_dict = {"id": "13", "name": "Eminem"}
    domain_art = p.to_domain_artist(art_dict)
    assert domain_art.id == "13"
    assert domain_art.name == "Eminem"
    assert domain_art.provider == "deezer"

    # Album dict mapping
    alb_dict = {
        "id": "302127",
        "title": "The Eminem Show",
        "upc": "0060694932902",
        "release_date": "2002-05-26",
        "artist": art_dict,
    }
    domain_alb = p.to_domain_album(alb_dict)
    assert domain_alb is not None
    assert domain_alb.id == "302127"
    assert domain_alb.title == "The Eminem Show"
    assert domain_alb.upc == "0060694932902"
    assert domain_alb.release_date == "2002-05-26"
    assert len(domain_alb.artists) == 1
    assert domain_alb.artists[0].name == "Eminem"


def test_domain_mapping_track_and_playlist(tmp_path: Path):
    cfg = ConfigManager(custom_config_dir=tmp_path)
    p = DeezerProvider(cfg=cfg)

    track_dict = {
        "id": "916424",
        "title": "Without Me",
        "duration": 290,
        "isrc": "USIR10200407",
        "readable": True,
        "artist": {"id": "13", "name": "Eminem"},
        "contributors": [
            {"id": "13", "name": "Eminem"},
            {"id": "55", "name": "Dr. Dre"},
        ],
        "album": {
            "id": "302127",
            "title": "The Eminem Show",
            "upc": "0060694932902",
        },
    }
    domain_t = p.to_domain_track(track_dict)
    assert domain_t.id == "916424"
    assert domain_t.title == "Without Me"
    assert domain_t.duration_seconds == 290
    assert domain_t.isrc == "USIR10200407"
    assert domain_t.artist.name == "Eminem"
    assert len(domain_t.collaborating_artists) == 1
    assert domain_t.collaborating_artists[0].name == "Dr. Dre"
    assert domain_t.album is not None
    assert domain_t.album.title == "The Eminem Show"
    assert domain_t.available is True

    # Playlist dict mapping
    pl_dict = {
        "id": "11161242",
        "title": "Hip Hop Classics",
        "description": "The best 90s and 2000s rap",
        "public": True,
        "nb_tracks": 50,
        "duration": 12000,
    }
    domain_pl = p.to_domain_playlist(pl_dict)
    assert domain_pl.id == "11161242"
    assert domain_pl.title == "Hip Hop Classics"
    assert domain_pl.description == "The best 90s and 2000s rap"
    assert domain_pl.is_public is True
    assert domain_pl.track_count == 50
    assert domain_pl.duration_seconds == 12000


def test_get_user_playlists(tmp_path: Path, mock_deezer_session):
    cfg = ConfigManager(custom_config_dir=tmp_path)
    p = DeezerProvider(session=mock_deezer_session, cfg=cfg)

    pl_mock1 = MagicMock(id=1, title="Favorites", description="My favs", public=True, nb_tracks=25, duration=3600)
    pl_mock2 = MagicMock(id=2, title="Chill", description="Relaxing tunes", public=False, nb_tracks=15, duration=2400)

    user_mock = mock_deezer_session.get_user.return_value
    user_mock.get_playlists.return_value = [pl_mock1, pl_mock2]

    playlists = p.get_user_playlists()
    assert len(playlists) == 2
    assert playlists[0].title == "Favorites"
    assert playlists[0].track_count == 25
    assert playlists[1].title == "Chill"
    assert playlists[1].is_public is False


def test_get_playlist_by_id_and_title(tmp_path: Path, mock_deezer_session):
    cfg = ConfigManager(custom_config_dir=tmp_path)
    p = DeezerProvider(session=mock_deezer_session, cfg=cfg)

    pl_mock = MagicMock(id=999, title="Gym Hits", description="Workout", public=True, nb_tracks=10, duration=1800)
    pl_mock.get_tracks.return_value = [MagicMock()] * 10
    mock_deezer_session.get_playlist.return_value = pl_mock

    # Fetch by numeric ID
    pl_by_id = p.get_playlist("999")
    assert pl_by_id is not None
    assert pl_by_id.id == "999"
    assert pl_by_id.title == "Gym Hits"

    # Fetch by title
    mock_deezer_session.get_playlist.side_effect = [pl_mock, Exception("Not found")]
    user_mock = mock_deezer_session.get_user.return_value
    user_mock.get_playlists.return_value = [pl_mock]

    pl_by_title = p.get_playlist("Gym Hits")
    assert pl_by_title is not None
    assert pl_by_title.title == "Gym Hits"


def test_get_playlist_tracks(tmp_path: Path, mock_deezer_session):
    cfg = ConfigManager(custom_config_dir=tmp_path)
    p = DeezerProvider(session=mock_deezer_session, cfg=cfg)

    t1 = MagicMock(id=101, title="Song One", duration=180, isrc="ISRC01", readable=True)
    t1.artist.name = "Artist A"
    t1.album.title = "Album A"
    t1.album.upc = "UPC01"

    t2 = MagicMock(id=102, title="Song Two", duration=200, isrc="ISRC02", readable=False)
    t2.artist.name = "Artist B"
    t2.album.title = "Album B"
    t2.album.upc = "UPC02"

    pl_mock = MagicMock(id=555)
    pl_mock.get_tracks.return_value = [t1, t2]
    mock_deezer_session.get_playlist.return_value = pl_mock

    tracks = p.get_playlist_tracks("555")
    assert len(tracks) == 2
    assert tracks[0].title == "Song One"
    assert tracks[0].artist.name == "Artist A"
    assert tracks[0].available is True
    assert tracks[1].title == "Song Two"
    assert tracks[1].available is False


def test_create_playlist(tmp_path: Path, mock_deezer_session):
    cfg = ConfigManager(custom_config_dir=tmp_path)
    p = DeezerProvider(session=mock_deezer_session, cfg=cfg)

    mock_deezer_session.create_playlist.return_value = 888
    pl_mock = MagicMock(id=888, title="New Mix", description="Cool tracks", public=False, nb_tracks=0, duration=0)
    mock_deezer_session.get_playlist.return_value = pl_mock

    new_pl = p.create_playlist("New Mix", description="Cool tracks", is_public=False)
    assert new_pl.id == "888"
    assert new_pl.title == "New Mix"
    mock_deezer_session.create_playlist.assert_called_once_with("New Mix")


def test_add_tracks_to_playlist(tmp_path: Path, mock_deezer_session):
    cfg = ConfigManager(custom_config_dir=tmp_path)
    p = DeezerProvider(session=mock_deezer_session, cfg=cfg)

    pl_mock = MagicMock(id=777)
    pl_mock.add_tracks.return_value = True
    mock_deezer_session.get_playlist.return_value = pl_mock

    assert p.add_tracks_to_playlist("777", ["101", "102"]) is True
    pl_mock.add_tracks.assert_called_once_with([101, 102])


def test_remove_track_from_playlist_by_index(tmp_path: Path, mock_deezer_session):
    cfg = ConfigManager(custom_config_dir=tmp_path)
    p = DeezerProvider(session=mock_deezer_session, cfg=cfg)

    t1 = MagicMock(id=101, title="Track 1")
    t2 = MagicMock(id=102, title="Track 2")
    pl_mock = MagicMock(id=777)
    pl_mock.get_tracks.return_value = [t1, t2]
    pl_mock.delete_tracks.return_value = True
    mock_deezer_session.get_playlist.return_value = pl_mock

    assert p.remove_track_from_playlist_by_index("777", 1) is True
    pl_mock.delete_tracks.assert_called_once_with([102])

    with pytest.raises(ValueError, match="out of range"):
        p.remove_track_from_playlist_by_index("777", 5)


def test_clear_playlist(tmp_path: Path, mock_deezer_session):
    cfg = ConfigManager(custom_config_dir=tmp_path)
    p = DeezerProvider(session=mock_deezer_session, cfg=cfg)

    t1 = MagicMock(id=101)
    t2 = MagicMock(id=102)
    pl_mock = MagicMock(id=777)
    pl_mock.get_tracks.return_value = [t1, t2]
    pl_mock.delete_tracks.return_value = True
    mock_deezer_session.get_playlist.return_value = pl_mock

    assert p.clear_playlist("777") is True
    pl_mock.delete_tracks.assert_called_once_with([101, 102])


def test_curated_mixes_and_tracks(tmp_path: Path, mock_deezer_session):
    cfg = ConfigManager(custom_config_dir=tmp_path)
    p = DeezerProvider(session=mock_deezer_session, cfg=cfg)

    mixes = p.get_curated_mixes()
    assert len(mixes) == 2
    assert any(m.id == "flow" for m in mixes)
    assert any(m.id == "recommended" for m in mixes)

    t_flow = MagicMock(id=201, title="Flow Song", duration=210, isrc="USFLOW1")
    t_flow.artist.name = "Flow Artist"
    mock_deezer_session.get_user_flow.return_value = [t_flow]

    tracks = p.get_mix_tracks("flow")
    assert len(tracks) == 1
    assert tracks[0].title == "Flow Song"


def test_catalog_lookup_and_search(tmp_path: Path, mock_deezer_session):
    cfg = ConfigManager(custom_config_dir=tmp_path)
    p = DeezerProvider(session=mock_deezer_session, cfg=cfg)

    # 1. get_track
    t_mock = MagicMock(id=501, title="Song 501", duration=240, isrc="US501")
    t_mock.artist.name = "Artist 501"
    mock_deezer_session.get_track.return_value = t_mock

    t = p.get_track("501")
    assert t is not None
    assert t.id == "501"
    assert t.title == "Song 501"

    # 2. get_tracks_by_isrc (direct hit)
    mock_deezer_session.request.return_value = t_mock
    isrc_tracks = p.get_tracks_by_isrc("US501")
    assert len(isrc_tracks) == 1
    assert isrc_tracks[0].id == "501"

    # 3. get_album and get_albums_by_barcode
    alb_mock = MagicMock(id=301, title="Great Album", upc="123456789012", release_date="2020-01-01")
    alb_mock.artist.name = "Album Artist"
    mock_deezer_session.get_album.return_value = alb_mock
    mock_deezer_session.request.return_value = alb_mock

    alb = p.get_album("301")
    assert alb is not None
    assert alb.title == "Great Album"

    bar_albs = p.get_albums_by_barcode("123456789012")
    assert len(bar_albs) == 1
    assert bar_albs[0].upc == "123456789012"

    # 4. get_album_tracks
    alb_mock.get_tracks.return_value = [t_mock]
    alb_tracks = p.get_album_tracks("301")
    assert len(alb_tracks) == 1
    assert alb_tracks[0].title == "Song 501"

    # 5. search_tracks
    mock_deezer_session.search.return_value = [t_mock]
    search_res = p.search_tracks("Song 501", limit=5)
    assert len(search_res) == 1
    assert search_res[0].title == "Song 501"


def test_cli_provider_and_auth_deezer(tmp_path: Path, monkeypatch, mock_deezer_session):
    temp_cfg = ConfigManager(custom_config_dir=tmp_path)
    monkeypatch.setitem(state, "config", temp_cfg)

    runner = CliRunner()

    # 1. curo provider list shows Deezer as Active
    res_list = runner.invoke(app, ["provider", "list"])
    assert res_list.exit_code == 0
    assert "deezer" in res_list.stdout
    assert "Active" in res_list.stdout

    # 2. curo auth status for deezer when not logged in
    res_status = runner.invoke(app, ["auth", "status", "-p", "deezer"])
    assert res_status.exit_code == 0
    assert "Not authenticated on Deezer" in res_status.stdout