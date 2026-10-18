"""Tests for TidalSessionManager and credential handling."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from curo.config import ConfigManager
from curo.providers.tidal.session import TidalAuthenticationError, TidalSessionManager


def test_load_session_missing_file(tmp_path: Path):
    cfg = ConfigManager(custom_config_dir=tmp_path)
    mgr = TidalSessionManager(cfg)

    with pytest.raises(TidalAuthenticationError, match="No session file found"):
        mgr.load_session()


def test_save_and_load_valid_session(tmp_path: Path):
    cfg = ConfigManager(custom_config_dir=tmp_path)
    mgr = TidalSessionManager(cfg)

    mock_session = MagicMock()
    mock_session.token_type = "Bearer"
    mock_session.session_id = "test-session-id"
    mock_session.access_token = "valid-access-token"
    mock_session.refresh_token = "valid-refresh-token"
    mock_session.is_pkce = True
    mock_session.user.id = 12345
    mock_session.check_login.return_value = True

    mgr.save_session(mock_session)

    session_file = cfg.get_provider_session_file("tidal")
    assert session_file.exists()
    saved_data = json.loads(session_file.read_text(encoding="utf-8"))
    assert saved_data["access_token"]["data"] == "valid-access-token"

    with patch("tidalapi.Session") as MockTidalSession:
        inst = MockTidalSession.return_value
        inst.load_oauth_session.return_value = True
        inst.check_login.return_value = True

        loaded = mgr.load_session(auto_refresh=False)
        assert loaded == inst
        inst.load_oauth_session.assert_called_once_with(
            token_type="Bearer",
            access_token="valid-access-token",
            refresh_token="valid-refresh-token",
            is_pkce=True,
        )


def test_silent_token_refresh_on_expired_session(tmp_path: Path):
    cfg = ConfigManager(custom_config_dir=tmp_path)
    mgr = TidalSessionManager(cfg)
    session_file = cfg.get_provider_session_file("tidal")

    session_data = {
        "token_type": {"data": "Bearer"},
        "access_token": {"data": "expired-access-token"},
        "refresh_token": {"data": "valid-refresh-token"},
        "is_pkce": {"data": True},
    }
    cfg.ensure_config_dir()
    session_file.write_text(json.dumps(session_data), encoding="utf-8")

    with patch("tidalapi.Session") as MockTidalSession:
        inst = MockTidalSession.return_value
        inst.load_oauth_session.side_effect = [False, True]
        inst.check_login.return_value = True
        inst.token_refresh.return_value = True
        inst.token_type = "Bearer"
        inst.session_id = "new-session-id"
        inst.access_token = "new-access-token"
        inst.refresh_token = "new-refresh-token"
        inst.is_pkce = True
        inst.user.id = 99999

        loaded = mgr.load_session(auto_refresh=True)
        assert loaded == inst
        inst.token_refresh.assert_called_once_with("valid-refresh-token")


def test_logout_removes_file(tmp_path: Path):
    cfg = ConfigManager(custom_config_dir=tmp_path)
    mgr = TidalSessionManager(cfg)
    session_file = cfg.get_provider_session_file("tidal")

    cfg.ensure_config_dir()
    session_file.write_text("{}", encoding="utf-8")
    assert session_file.exists()

    assert mgr.logout() is True
    assert not session_file.exists()
    assert mgr.logout() is False