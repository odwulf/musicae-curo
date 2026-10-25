"""Unit tests for ProviderRegistry and provider adapter instantiation."""

import pytest

from curo.config import ConfigManager
from curo.core.registry import ProviderRegistry
import curo.providers  # Ensure all adapters are loaded
from curo.providers.base import ProviderStub
from curo.providers.deezer import DeezerProvider
from curo.providers.spotify import SpotifyProvider
from curo.providers.tidal import TidalProvider


def test_list_registered_providers():
    providers = ProviderRegistry.list_providers()
    assert "tidal" in providers
    assert "spotify" in providers
    assert "deezer" in providers
    assert "qobuz" in providers
    assert "ytmusic" in providers


def test_instantiate_active_providers(tmp_path):
    cfg = ConfigManager(custom_config_dir=tmp_path)
    p_tidal = ProviderRegistry.create_provider("tidal", cfg=cfg)
    assert isinstance(p_tidal, TidalProvider)

    p_spotify = ProviderRegistry.create_provider("spotify", cfg=cfg)
    assert isinstance(p_spotify, SpotifyProvider)

    p_deezer = ProviderRegistry.create_provider("deezer", cfg=cfg)
    assert isinstance(p_deezer, DeezerProvider)


def test_instantiate_stub_providers_with_config(tmp_path):
    cfg = ConfigManager(custom_config_dir=tmp_path)
    stub_names = ["qobuz", "ytmusic"]

    for name in stub_names:
        provider = ProviderRegistry.create_provider(name, cfg=cfg)
        assert isinstance(provider, ProviderStub)
        assert provider.name in (name, "youtube_music")
        assert provider.is_authenticated() is False

        session_info = provider.get_session_info()
        assert session_info["logged_in"] is False
        assert session_info["status"] == "scaffolded_stub"

        with pytest.raises(NotImplementedError, match="not yet implemented"):
            provider.login()


def test_create_unknown_provider():
    with pytest.raises(ValueError, match="Unknown or unsupported provider"):
        ProviderRegistry.create_provider("applemusic")


def test_cli_stub_provider_messages(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from curo.cli import app, state

    temp_cfg = ConfigManager(custom_config_dir=tmp_path)
    monkeypatch.setitem(state, "config", temp_cfg)

    runner = CliRunner()

    # 1. auth login on stub provider (e.g. qobuz)
    res_login = runner.invoke(app, ["auth", "login", "-p", "qobuz"])
    assert res_login.exit_code == 1
    assert "Qobuz adapter is scaffolded, but not yet implemented" in res_login.stdout

    # 2. auth status on stub provider
    res_status = runner.invoke(app, ["auth", "status", "-p", "qobuz"])
    assert res_status.exit_code == 0
    assert "Qobuz is scaffolded (stub) and not yet implemented" in res_status.stdout

    # 3. playlist list on stub provider
    res_playlist = runner.invoke(app, ["playlist", "list", "-p", "qobuz"])
    assert res_playlist.exit_code == 1
    assert "Provider 'Qobuz' is scaffolded and not yet implemented" in (res_playlist.stdout + (res_playlist.stderr or ""))
