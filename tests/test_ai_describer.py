"""Tests for AI thematic description service and mix naming integration."""

from unittest.mock import MagicMock, patch
import json
import urllib.error
import pytest

from curo.core.models import Track, Artist, Album
from curo.config import ConfigManager, AIConfig
from curo.services.ai_describer import AIDescriber, MixDescription
from curo.services.mix_service import MixService
from curo.services.playlist_service import PlaylistService
from curo.core.provider import MusicProvider


def test_ai_describer_is_configured():
    cfg = ConfigManager()
    with patch.object(cfg, "get_ai_config") as mock_get_cfg:
        mock_get_cfg.return_value = AIConfig(enabled=False, api_key="secret")
        describer = AIDescriber(cfg=cfg)
        assert describer.is_enabled() is False

        mock_get_cfg.return_value = AIConfig(enabled=True, api_key=None)
        describer = AIDescriber(cfg=cfg)
        assert describer.is_enabled() is False

        mock_get_cfg.return_value = AIConfig(enabled=True, api_key="valid-key")
        describer = AIDescriber(cfg=cfg)
        assert describer.is_enabled() is True


def test_ai_describer_gemini_success():
    cfg = ConfigManager()
    with patch.object(cfg, "get_ai_config") as mock_get_cfg:
        mock_get_cfg.return_value = AIConfig(enabled=True, api_key="fake-key", model="gemini-flash-latest")
        describer = AIDescriber(cfg=cfg)

        tracks = [
            Track(
                id="1",
                title="Song A",
                artist=Artist(id="a1", name="Artist One"),
                album=Album(id="al1", title="Album X", release_date="2021-05-01"),
            ),
            Track(
                id="2",
                title="Song B",
                artist=Artist(id="a2", name="Artist Two"),
                album=Album(id="al2", title="Album Y", release_date="2022-09-15"),
            ),
        ]

        mock_gemini_response = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "text": json.dumps({
                                    "theme_title": "2026-11-14: Ambient Neoclassical & Chamber Folk",
                                    "description": "Discovery mix backup: Ethereal string arrangements and gentle acoustics (Artist One, Artist Two)."
                                })
                            }
                        ]
                    }
                }
            ]
        }

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps(mock_gemini_response).encode("utf-8")
            mock_urlopen.return_value.__enter__.return_value = mock_resp

            res = describer.describe_tracks(tracks)

            assert res is not None
            # Verified date prefix was stripped from theme_title
            assert res.theme_title == "Ambient Neoclassical & Chamber Folk"
            assert res.description.startswith("Discovery mix backup:")
            assert "(Artist One, Artist Two)" in res.description

            # Verify request headers and URL
            req_call = mock_urlopen.call_args[0][0]
            assert "gemini-flash-latest" in req_call.get_full_url()
            assert "fake-key" in req_call.get_full_url()
            assert req_call.headers.get("User-agent") == "Curo/1.0 (Universal Music Library Custodian)"


def test_ai_describer_gemini_error_graceful_fallback():
    cfg = ConfigManager()
    with patch.object(cfg, "get_ai_config") as mock_get_cfg:
        mock_get_cfg.return_value = AIConfig(enabled=True, api_key="fake-key")
        describer = AIDescriber(cfg=cfg)

        tracks = [Track(id="1", title="Song A", artist=Artist(id="a1", name="Artist One"))]

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = urllib.error.HTTPError(
                url="https://api", code=503, msg="Service Unavailable", hdrs={}, fp=None
            )

            res = describer.describe_tracks(tracks)
            assert res is None


def test_mix_service_backup_daily_discovery_with_ai():
    mock_provider = MagicMock(spec=MusicProvider)
    mock_provider.name = "tidal"
    mock_provider.display_name = "TIDAL"

    disc_mix = MagicMock()
    disc_mix.id = "disc-123"
    disc_mix.title = "Daily Discovery"
    disc_mix.mix_type = "discovery_mix"

    tracks = [
        Track(id="t1", title="Ocean Lullaby", artist=Artist(id="a1", name="Seafolk")),
        Track(id="t2", title="Mountain Chant", artist=Artist(id="a2", name="Highlanders")),
    ]
    mock_provider.get_mix_tracks.return_value = tracks

    svc = MixService(provider=mock_provider)
    svc.get_categorized_daily_mixes = MagicMock(return_value=(disc_mix, []))
    svc.playlist_service.get_or_create_playlist = MagicMock()
    mock_pl = MagicMock()
    mock_pl.id = "pl-target"
    svc.playlist_service.get_or_create_playlist.return_value = mock_pl
    svc.playlist_service.add_tracks = MagicMock(return_value=2)
    mock_provider.get_playlist_tracks.return_value = []

    with patch("curo.services.ai_describer.AIDescriber.describe_tracks") as mock_describe, \
         patch("curo.services.ai_describer.AIDescriber.is_enabled", return_value=True):

        mock_describe.return_value = MixDescription(
            theme_title="Nautical Ambient & Celtic Harmonies",
            description="Discovery mix backup: Ocean field recordings and acoustic polyphony (Seafolk, Highlanders)."
        )

        res = svc.backup_daily_discovery(ai_naming=True)

        assert res["success"] is True
        call_args = svc.playlist_service.get_or_create_playlist.call_args[1]
        assert "Nautical Ambient & Celtic Harmonies" in call_args["title"]
        assert "Discovery mix backup: Ocean field recordings" in call_args["description"]


def test_sync_mixes_preceding_backup_with_ai(tmp_path):
    cfg = ConfigManager(custom_config_dir=tmp_path)
    mock_provider = MagicMock(spec=MusicProvider)
    mock_provider.name = "tidal"
    mock_provider.display_name = "TIDAL"

    disc_mix = MagicMock()
    disc_mix.id = "disc-123"
    disc_mix.title = "Daily Discovery"
    disc_mix.mix_type = "discovery_mix"

    current_tracks = [
        Track(id="t3", title="New Track", artist=Artist(id="a3", name="New Artist")),
    ]
    mock_provider.get_mix_tracks.return_value = current_tracks
    mock_provider.get_playlist_tracks.return_value = []

    svc = MixService(provider=mock_provider, cfg=cfg)
    svc.get_categorized_daily_mixes = MagicMock(return_value=(disc_mix, []))

    # Pre-populate cache with preceding mix
    cached_preceding = {
        "id": "disc-123",
        "title": "Daily Discovery",
        "last_updated_date": "2026-11-13",
        "content_hash": "old_hash_12345",
        "tracks": [
            {"id": "t1", "title": "Old Track 1", "artist_name": "Old Artist 1"},
            {"id": "t2", "title": "Old Track 2", "artist_name": "Old Artist 2"},
        ]
    }
    svc.save_cached_mix_json("daily_discovery", cached_preceding)
    svc.playlist_service.get_or_create_playlist = MagicMock()
    mock_pl = MagicMock()
    mock_pl.id = "pl-backup"
    svc.playlist_service.get_or_create_playlist.return_value = mock_pl
    svc.playlist_service.add_tracks = MagicMock(return_value=2)

    with patch("curo.services.ai_describer.AIDescriber.describe_tracks") as mock_describe, \
         patch("curo.services.ai_describer.AIDescriber.is_enabled", return_value=True):

        mock_describe.return_value = MixDescription(
            theme_title="Psych-Pop & Vintage Surf",
            description="Discovery mix backup: Reverb-drenched surf guitars (Old Artist 1, Old Artist 2)."
        )

        res = svc.sync_mixes(backup_discovery=True, update_megamixes=False, ai_naming=True)

        assert res["success"] is True
        assert res["discovery_backup"] is not None
        call_args = svc.playlist_service.get_or_create_playlist.call_args[1]
        assert "2026-11-13: Psych-Pop & Vintage Surf" == call_args["title"]
        assert "Discovery mix backup: Reverb-drenched surf guitars (Old Artist 1, Old Artist 2)." == call_args["description"]


def test_playlist_service_update_playlist_metadata():
    mock_provider = MagicMock(spec=MusicProvider)
    mock_provider.name = "tidal"
    mock_pl = MagicMock()
    mock_pl.id = "pl-999"
    mock_pl.title = "Old Name"
    mock_provider.get_playlist.return_value = mock_pl
    mock_provider.update_playlist_metadata.return_value = True

    svc = PlaylistService(provider=mock_provider)
    success = svc.update_playlist_metadata(
        identifier="pl-999",
        title="New Name",
        description="New Description",
    )
    assert success is True
    mock_provider.update_playlist_metadata.assert_called_once_with(
        "pl-999", title="New Name", description="New Description"
    )

