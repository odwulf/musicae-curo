"""Comprehensive unit and integration tests for SpotifyProvider and SpotifySessionManager."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from typer.testing import CliRunner

from curo.cli import app, state
from curo.config import ConfigManager
from curo.core.models import Album, Artist, CuratedMix, Playlist, Track
from curo.core.registry import ProviderRegistry
from curo.providers.spotify.adapter import SpotifyProvider
from curo.providers.spotify.session import SpotifyAuthenticationError, SpotifySessionManager


@pytest.fixture
def mock_spotify_session():
    mock = MagicMock()
    mock.current_user.return_value = {
        "id": "spotify_user_123",
        "display_name": "Test User",
        "country": "FR",
    }
    mock.me.return_value = {
        "id": "spotify_user_123",
        "display_name": "Test User",
    }
    return mock


def test_spotify_provider_registration(tmp_path: Path):
    cfg = ConfigManager(custom_config_dir=tmp_path)
    p = ProviderRegistry.create_provider("spotify", cfg=cfg)
    assert isinstance(p, SpotifyProvider)
    assert p.name == "spotify"
    assert p.display_name == "Spotify"


def test_spotify_session_manager_credentials_and_status(tmp_path: Path, monkeypatch):
    cfg = ConfigManager(custom_config_dir=tmp_path)
    sm = SpotifySessionManager(cfg)

    # 1. No credentials configured
    assert sm.get_status()["logged_in"] is False

    # 2. Save credentials in config.json
    sm.save_credentials("client_id_123", "client_secret_abc")
    c_id, c_secret, r_uri = sm.get_credentials()
    assert c_id == "client_id_123"
    assert c_secret == "client_secret_abc"
    assert r_uri == "http://127.0.0.1:8888/callback"

    # 3. Environment variable override
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "env_client_id")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "env_client_secret")
    c_id_env, c_secret_env, _ = sm.get_credentials()
    assert c_id_env == "env_client_id"
    assert c_secret_env == "env_client_secret"

    # 4. Missing session file raises SpotifyAuthenticationError on load_session
    with pytest.raises(SpotifyAuthenticationError, match="No Spotify session found"):
        sm.load_session()

    # 5. Logout removes session file
    sm.session_file.write_text("{}", encoding="utf-8")
    assert sm.session_file.exists()
    assert sm.logout() is True
    assert not sm.session_file.exists()


def test_domain_mapping_artist_and_album(tmp_path: Path):
    cfg = ConfigManager(custom_config_dir=tmp_path)
    p = SpotifyProvider(cfg=cfg)

    # Artist dict mapping
    art_dict = {"id": "art_1", "name": "Queen"}
    domain_art = p.to_domain_artist(art_dict)
    assert domain_art.id == "art_1"
    assert domain_art.name == "Queen"
    assert domain_art.provider == "spotify"

    # Album dict mapping
    alb_dict = {
        "id": "alb_1",
        "name": "A Night at the Opera",
        "external_ids": {"upc": "0077774620725"},
        "release_date": "1975-11-21",
        "artists": [art_dict],
    }
    domain_alb = p.to_domain_album(alb_dict)
    assert domain_alb is not None
    assert domain_alb.id == "alb_1"
    assert domain_alb.title == "A Night at the Opera"
    assert domain_alb.upc == "0077774620725"
    assert domain_alb.release_date == "1975-11-21"
    assert len(domain_alb.artists) == 1
    assert domain_alb.artists[0].name == "Queen"


def test_domain_mapping_track(tmp_path: Path):
    cfg = ConfigManager(custom_config_dir=tmp_path)
    p = SpotifyProvider(cfg=cfg)

    track_dict = {
        "id": "trk_1",
        "name": "Bohemian Rhapsody",
        "duration_ms": 354000,
        "artists": [
            {"id": "art_1", "name": "Queen"},
            {"id": "art_2", "name": "David Bowie"},
        ],
        "album": {
            "id": "alb_1",
            "name": "A Night at the Opera",
            "external_ids": {"upc": "0077774620725"},
            "release_date": "1975-11-21",
        },
        "external_ids": {"isrc": "GBUM71029604"},
        "is_playable": True,
    }

    domain_trk = p.to_domain_track(track_dict)
    assert domain_trk.id == "trk_1"
    assert domain_trk.title == "Bohemian Rhapsody"
    assert domain_trk.duration_seconds == 354
    assert domain_trk.artist.name == "Queen"
    assert len(domain_trk.collaborating_artists) == 1
    assert domain_trk.collaborating_artists[0].name == "David Bowie"
    assert domain_trk.isrc == "GBUM71029604"
    assert domain_trk.album.title == "A Night at the Opera"
    assert domain_trk.available is True


def test_domain_mapping_unavailable_track(tmp_path: Path):
    cfg = ConfigManager(custom_config_dir=tmp_path)
    p = SpotifyProvider(cfg=cfg)

    restricted_track = {
        "id": "trk_unavail",
        "name": "Restricted Track",
        "duration_ms": 180000,
        "artists": [{"id": "art_3", "name": "Unknown Artist"}],
        "restrictions": {"reason": "market"},
    }
    trk = p.to_domain_track(restricted_track)
    assert trk.available is False
    assert trk.allow_streaming is False


def test_get_user_playlists_with_pagination(tmp_path: Path, mock_spotify_session):
    page1 = {
        "items": [
            {"id": "pl_1", "name": "Favorites", "description": "My favorites", "public": True, "tracks": {"total": 10}},
        ],
        "next": "https://api.spotify.com/v1/me/playlists?offset=50&limit=50",
    }
    page2 = {
        "items": [
            {"id": "pl_2", "name": "Workout", "description": "Gym tunes", "public": False, "tracks": {"total": 25}},
        ],
        "next": None,
    }

    mock_spotify_session.current_user_playlists.return_value = page1
    mock_spotify_session.next.return_value = page2

    cfg = ConfigManager(custom_config_dir=tmp_path)
    p = SpotifyProvider(session=mock_spotify_session, cfg=cfg)

    playlists = p.get_user_playlists()
    assert len(playlists) == 2
    assert playlists[0].title == "Favorites"
    assert playlists[0].track_count == 10
    assert playlists[1].title == "Workout"
    assert playlists[1].track_count == 25


def test_get_playlist_by_id_and_by_title(tmp_path: Path, mock_spotify_session):
    mock_spotify_session.playlist.side_effect = lambda pid: (
        {"id": "pl_1", "name": "Direct Playlist", "description": "", "public": True, "tracks": {"total": 5}}
        if pid == "pl_1" else None
    )
    mock_spotify_session.current_user_playlists.return_value = {
        "items": [
            {"id": "pl_2", "name": "Summer Vibes", "description": "", "public": True, "tracks": {"total": 15}},
        ],
        "next": None,
    }

    cfg = ConfigManager(custom_config_dir=tmp_path)
    p = SpotifyProvider(session=mock_spotify_session, cfg=cfg)

    # 1. By ID
    pl_by_id = p.get_playlist("pl_1")
    assert pl_by_id is not None
    assert pl_by_id.title == "Direct Playlist"

    # 2. By title
    pl_by_title = p.get_playlist("summer vibes")
    assert pl_by_title is not None
    assert pl_by_title.id == "pl_2"


def test_get_playlist_tracks(tmp_path: Path, mock_spotify_session):
    items_page = {
        "items": [
            {
                "track": {
                    "id": "t1",
                    "name": "Track One",
                    "duration_ms": 200000,
                    "artists": [{"id": "a1", "name": "Artist One"}],
                    "type": "track",
                }
            },
            {
                # Exclude podcast episode
                "track": {
                    "id": "ep1",
                    "name": "Podcast Episode",
                    "type": "episode",
                }
            },
        ],
        "next": None,
    }
    mock_spotify_session.playlist_items.return_value = items_page

    cfg = ConfigManager(custom_config_dir=tmp_path)
    p = SpotifyProvider(session=mock_spotify_session, cfg=cfg)

    tracks = p.get_playlist_tracks("pl_1")
    assert len(tracks) == 1
    assert tracks[0].id == "t1"
    assert tracks[0].title == "Track One"


def test_create_and_clear_playlist(tmp_path: Path, mock_spotify_session):
    mock_spotify_session.user_playlist_create.return_value = {
        "id": "pl_new",
        "name": "Brand New Playlist",
        "description": "Created for testing",
        "public": False,
        "tracks": {"total": 0},
    }

    cfg = ConfigManager(custom_config_dir=tmp_path)
    p = SpotifyProvider(session=mock_spotify_session, cfg=cfg)

    created = p.create_playlist("Brand New Playlist", description="Created for testing")
    assert created.id == "pl_new"
    assert created.title == "Brand New Playlist"
    mock_spotify_session.user_playlist_create.assert_called_once_with(
        user="spotify_user_123", name="Brand New Playlist", public=False, description="Created for testing"
    )

    # Clear playlist
    assert p.clear_playlist("pl_new") is True
    mock_spotify_session.playlist_replace_items.assert_called_once_with("pl_new", [])


def test_add_tracks_chunking_and_remove_by_index(tmp_path: Path, mock_spotify_session):
    cfg = ConfigManager(custom_config_dir=tmp_path)
    p = SpotifyProvider(session=mock_spotify_session, cfg=cfg)

    # 1. Add tracks (150 tracks -> 2 chunks of 100 and 50)
    track_ids = [f"trk_{i}" for i in range(150)]
    assert p.add_tracks_to_playlist("pl_target", track_ids) is True
    assert mock_spotify_session.playlist_add_items.call_count == 2
    first_call_args = mock_spotify_session.playlist_add_items.call_args_list[0][0]
    assert len(first_call_args[1]) == 100
    assert first_call_args[1][0] == "spotify:track:trk_0"

    # 2. Remove by index
    mock_spotify_session.playlist_items.return_value = {
        "items": [{"track": {"id": "trk_target", "uri": "spotify:track:trk_target"}}]
    }
    assert p.remove_track_from_playlist_by_index("pl_target", 3) is True
    mock_spotify_session.playlist_remove_specific_occurrences_of_items.assert_called_once_with(
        "pl_target", [{"uri": "spotify:track:trk_target", "positions": [3]}]
    )


def test_catalog_search_and_isrc_barcode_lookup(tmp_path: Path, mock_spotify_session):
    cfg = ConfigManager(custom_config_dir=tmp_path)
    p = SpotifyProvider(session=mock_spotify_session, cfg=cfg)

    # 1. Search tracks
    mock_spotify_session.search.return_value = {
        "tracks": {
            "items": [
                {
                    "id": "trk_found",
                    "name": "Found Track",
                    "duration_ms": 180000,
                    "artists": [{"id": "art_1", "name": "Found Artist"}],
                    "external_ids": {"isrc": "USRC12345678"},
                }
            ]
        },
        "albums": {
            "items": [
                {
                    "id": "alb_found",
                    "name": "Found Album",
                    "external_ids": {"upc": "123456789012"},
                    "artists": [{"id": "art_1", "name": "Found Artist"}],
                }
            ]
        },
    }

    searched = p.search_tracks("Found Track", limit=5)
    assert len(searched) == 1
    assert searched[0].title == "Found Track"

    # 2. ISRC query
    by_isrc = p.get_tracks_by_isrc("USRC12345678")
    assert len(by_isrc) == 1
    assert by_isrc[0].isrc == "USRC12345678"

    # 3. Barcode query
    by_upc = p.get_albums_by_barcode("123456789012")
    assert len(by_upc) == 1
    assert by_upc[0].upc == "123456789012"


def test_curated_mixes_detection(tmp_path: Path, mock_spotify_session):
    mock_spotify_session.current_user_playlists.return_value = {
        "items": [
            {
                "id": "mix_dw",
                "name": "Discover Weekly",
                "owner": {"id": "spotify"},
                "tracks": {"total": 30},
            },
            {
                "id": "mix_dm1",
                "name": "Daily Mix 1",
                "owner": {"id": "spotify"},
                "tracks": {"total": 50},
            },
            {
                "id": "pl_user",
                "name": "Regular Playlist",
                "owner": {"id": "user_me"},
                "tracks": {"total": 12},
            },
        ],
        "next": None,
    }

    cfg = ConfigManager(custom_config_dir=tmp_path)
    p = SpotifyProvider(session=mock_spotify_session, cfg=cfg)

    mixes = p.get_curated_mixes()
    assert len(mixes) == 2
    titles = {m.title for m in mixes}
    assert "Discover Weekly" in titles
    assert "Daily Mix 1" in titles


def test_cli_provider_list_and_auth_status(tmp_path: Path, monkeypatch):
    temp_cfg = ConfigManager(custom_config_dir=tmp_path)
    monkeypatch.setitem(state, "config", temp_cfg)

    runner = CliRunner()

    # 1. Provider list displays both Tidal and Spotify as Active
    res_list = runner.invoke(app, ["provider", "list"])
    assert res_list.exit_code == 0
    assert "tidal" in res_list.stdout.lower()
    assert "spotify" in res_list.stdout.lower()
    assert "Active" in res_list.stdout

    # 2. Auth status when unauthenticated
    res_status = runner.invoke(app, ["auth", "status", "-p", "spotify"])
    assert res_status.exit_code == 0
    assert "Not authenticated on Spotify" in res_status.stdout


def test_spotify_login_403_premium_required_friendly_error(tmp_path: Path, monkeypatch):
    cfg = ConfigManager(custom_config_dir=tmp_path)
    sm = SpotifySessionManager(cfg)
    sm.save_credentials("client_id", "client_secret")

    # Mock token cache existing
    sm.session_file.write_text('{"access_token": "valid_tok", "refresh_token": "ref_tok", "expires_at": 9999999999}', encoding="utf-8")

    with patch("spotipy.Spotify") as MockSp:
        mock_instance = MockSp.return_value
        mock_instance.current_user.side_effect = Exception(
            "http status: 403, code: -1 - https://api.spotify.com/v1/me/: Active premium subscription required for the owner of the app."
        )

        with pytest.raises(SpotifyAuthenticationError, match="An active Spotify Premium subscription is required"):
            sm.load_session()


