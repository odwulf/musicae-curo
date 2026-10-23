"""Tests for EmailService, HTML templates, SMTP delivery, and notification CLI commands."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from curo.cli import app, state
from curo.config import ConfigManager, EmailConfig
from curo.services.email_service import EmailService


@pytest.fixture
def temp_config(tmp_path: Path) -> ConfigManager:
    return ConfigManager(custom_config_dir=tmp_path)


def test_email_config_from_file_and_env(temp_config: ConfigManager, monkeypatch):
    # Default unconfigured
    cfg = temp_config.get_email_config()
    assert cfg.is_configured is False
    assert cfg.smtp_port == 587

    # Save config to file
    custom_cfg = EmailConfig(
        smtp_host="mail.example.com",
        smtp_port=465,
        smtp_user="user@example.com",
        smtp_password="secretpassword",
        use_ssl=True,
        use_tls=False,
        from_address="noreply@example.com",
        to_address="listener@example.com",
    )
    temp_config.save_email_config(custom_cfg)

    loaded = temp_config.get_email_config()
    assert loaded.is_configured is True
    assert loaded.smtp_host == "mail.example.com"
    assert loaded.smtp_port == 465
    assert loaded.smtp_user == "user@example.com"
    assert loaded.to_address == "listener@example.com"

    # Environment variable overrides
    monkeypatch.setenv("CURO_SMTP_HOST", "smtp.override.org")
    monkeypatch.setenv("CURO_EMAIL_TO", "boss@example.com")
    env_cfg = temp_config.get_email_config()
    assert env_cfg.smtp_host == "smtp.override.org"
    assert env_cfg.to_address == "boss@example.com"


def test_first_email_tracking_and_state(temp_config: ConfigManager):
    assert temp_config.has_email_sent_before() is False

    temp_config.mark_email_sent("[Musicae Curo] Baseline Overview")
    assert temp_config.has_email_sent_before() is True

    # State file check
    with open(temp_config.notification_state_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert "first_email_sent_at" in data
    assert data["last_email_subject"] == "[Musicae Curo] Baseline Overview"

    first_time = data["first_email_sent_at"]

    # Mark second email sent: first_email_sent_at should not be overwritten
    temp_config.mark_email_sent("[Musicae Curo] Second Digest")
    with open(temp_config.notification_state_file, "r", encoding="utf-8") as f:
        data2 = json.load(f)
    assert data2["first_email_sent_at"] == first_time
    assert data2["last_email_subject"] == "[Musicae Curo] Second Digest"


def test_smtp_delivery_with_mocks(temp_config: ConfigManager):
    cfg = EmailConfig(
        smtp_host="smtp.test.com",
        smtp_port=587,
        smtp_user="testuser",
        smtp_password="testpassword",
        from_address="bot@test.com",
        to_address="me@test.com",
        use_tls=True,
    )
    temp_config.save_email_config(cfg)

    service = EmailService(cfg=temp_config)

    with patch("smtplib.SMTP") as mock_smtp_class:
        mock_server = MagicMock()
        mock_smtp_class.return_value = mock_server
        mock_server.__enter__.return_value = mock_server

        sent = service.send_email(
            subject="Test Subject",
            html_body="<p>Hello HTML</p>",
            text_body="Hello Text",
        )

        assert sent is True
        mock_smtp_class.assert_called_once_with("smtp.test.com", 587, timeout=30)
        mock_server.starttls.assert_called_once()
        mock_server.login.assert_called_once_with("testuser", "testpassword")
        mock_server.sendmail.assert_called_once()
        args, kwargs = mock_server.sendmail.call_args
        assert args[0] == "bot@test.com"
        assert args[1] == ["me@test.com"]
        assert "Subject: Test Subject" in args[2]


def test_render_all_playlists_report_contains_metrics_and_inventory(temp_config: ConfigManager):
    service = EmailService(cfg=temp_config)

    playlists = [
        {
            "name": "Rock Classics",
            "version": 1,
            "track_count": 50,
            "unavailable_tracks_count": 0,
            "unavailable_tracks": [],
        },
        {
            "name": "Heavy Rotation",
            "version": 2,
            "track_count": 30,
            "unavailable_tracks_count": 1,
            "unavailable_tracks": [
                {
                    "title": "Delisted Tune",
                    "artist": "Old Band",
                    "album": "Old Album",
                    "historical_isrc": "US1234567890",
                    "historical_upc": "123456789012",
                }
            ],
        },
    ]

    html_body, text_body = service.render_all_playlists_report(playlists)

    # HTML checks
    assert "Musicae Curo" in html_body
    assert "Baseline Overview" in html_body
    assert "Rock Classics" in html_body
    assert "Heavy Rotation" in html_body
    assert "Delisted Tune" in html_body
    assert "ISRC/UPC Cached" in html_body
    assert "Unavailable" in html_body

    # Text checks
    assert "Rock Classics" in text_body
    assert "Delisted Tune by Old Band" in text_body
    assert "Total Tracks: 80" in text_body


def test_render_changes_digest_shows_diff_and_version(temp_config: ConfigManager):
    service = EmailService(cfg=temp_config)

    modified_playlists = [
        {
            "name": "Rock Classics",
            "playlist_name": "Rock Classics",
            "version": 3,
            "track_diff": {
                "added_tracks": [
                    {"title": "New Track A", "artist": "Artist A"},
                    {"title": "New Track B", "artist": "Artist B"},
                ],
                "removed_tracks": [
                    {"title": "Dropped Track X", "artist": "Artist X"}
                ],
            },
            "new_alerts_count": 0,
        }
    ]

    html_body, text_body = service.render_changes_digest(modified_playlists)

    assert "Musicae Curo" in html_body
    assert "Rock Classics" in html_body
    assert "New Track A" in html_body
    assert "Dropped Track X" in html_body

    assert "Rock Classics" in text_body
    assert "+ Added: New Track A by Artist A" in text_body
    assert "- Removed: Dropped Track X by Artist X" in text_body


def test_render_changes_digest_shows_relinked_tracks_by_provider(temp_config: ConfigManager):
    service = EmailService(cfg=temp_config)

    modified_playlists = [
        {
            "name": "My Favorites",
            "playlist_name": "My Favorites",
            "provider": "tidal",
            "version": 5,
            "track_diff": {
                "added_tracks": [
                    {"title": "Genuine New Song", "artist": "New Artist"}
                ],
                "removed_tracks": [],
                "relinked_tracks": [
                    {
                        "old_id": "12345",
                        "new_id": "67890",
                        "title": "Bohemian Rhapsody",
                        "artist": "Queen",
                        "match_reason": "isrc",
                    }
                ],
            },
            "new_alerts_count": 0,
        }
    ]

    html_body, text_body = service.render_changes_digest(modified_playlists, provider_name="Tidal")

    # HTML assertions
    assert "↻ Relinked by TIDAL:" in html_body
    assert "Bohemian Rhapsody" in html_body
    assert "Queen" in html_body
    assert "12345" in html_body
    assert "67890" in html_body
    assert "Relinked" in html_body  # 4-column metric card
    assert "Genuine New Song" in html_body

    # Plain text assertions
    assert "* Relinked by TIDAL: Bohemian Rhapsody by Queen (ID: 12345 -> 67890)" in text_body
    assert "+ Added: Genuine New Song by New Artist" in text_body
    assert "Relinked: 1" in text_body


def test_render_changes_digest_excludes_exhaustive_diff_for_megamix(temp_config: ConfigManager):
    service = EmailService(cfg=temp_config)

    megamix_playlist = [
        {
            "name": "Daily Megamix",
            "playlist_name": "Daily Megamix",
            "version": 4,
            "track_count": 120,
            "track_diff": {
                "added_tracks": [
                    {"title": f"Added Track {i}", "artist": f"Artist {i}"} for i in range(1, 121)
                ],
                "removed_tracks": [
                    {"title": f"Removed Track {i}", "artist": f"Old Artist {i}"} for i in range(1, 119)
                ],
            },
            "unavailable_tracks": [
                {"title": "Unavailable Song", "artist": "Retired Artist"}
            ],
            "new_alerts_count": 1,
        }
    ]

    # Default: exclude_megamix_diff is True
    html_body, text_body = service.render_changes_digest(megamix_playlist)

    # Exhaustive 120-item listing must NOT be present
    assert "Added Track 50" not in html_body
    assert "Removed Track 50" not in html_body
    assert "Added Track 50" not in text_body
    assert "Removed Track 50" not in text_body

    # Compact summary must be present
    assert "Daily mix refreshed with 120 tracks" in html_body
    assert "Detailed track listing omitted" in html_body
    assert "Daily mix refreshed with 120 tracks" in text_body
    assert "detailed track list omitted" in text_body

    # Unavailable track alert should still be clearly shown
    assert "Unavailable Song" in html_body
    assert "Unavailable Song by Retired Artist" in text_body

    # When explicitly disabled: should render exhaustive listing
    cfg = service.email_config.model_copy()
    cfg.exclude_megamix_diff = False
    service.email_config = cfg

    html_full, text_full = service.render_changes_digest(megamix_playlist)
    assert "Added Track 50" in html_full
    assert "Removed Track 50" in html_full
    assert "+ Added: Added Track 50 by Artist 50" in text_full


def test_send_digest_notification_first_run_vs_subsequent(temp_config: ConfigManager):
    cfg = EmailConfig(
        smtp_host="smtp.test.com",
        smtp_port=587,
        from_address="bot@test.com",
        to_address="me@test.com",
    )
    temp_config.save_email_config(cfg)
    service = EmailService(cfg=temp_config)

    batch_data = {
        "playlists": [
            {
                "name": "Favorites",
                "playlist_name": "Favorites",
                "has_changed": False,
                "status": "unchanged",
                "version": 1,
                "track_count": 25,
                "unavailable_tracks_count": 0,
            }
        ],
        "total_alerts": 0,
    }

    with patch.object(service, "send_email", return_value=True) as mock_send:
        # 1. First run ever: should send all_playlists_baseline overview
        res1 = service.send_digest_notification(batch_data)
        assert res1["sent"] is True
        assert res1["template"] == "all_playlists_baseline"
        assert res1["is_first_email"] is True
        assert temp_config.has_email_sent_before() is True
        mock_send.assert_called_once()
        assert "Baseline" in mock_send.call_args[0][0]

        mock_send.reset_mock()

        # 2. Subsequent run with NO changes: should skip email
        res2 = service.send_digest_notification(batch_data)
        assert res2["sent"] is False
        assert res2["reason"] == "no_changes_detected"
        mock_send.assert_not_called()

        # 3. Subsequent run with force_all=True: should send all_playlists_baseline
        res3 = service.send_digest_notification(batch_data, force_all=True)
        assert res3["sent"] is True
        assert res3["template"] == "all_playlists_baseline"
        mock_send.assert_called_once()

        mock_send.reset_mock()

        # 4. Subsequent run with playlist modification: should send changes_digest
        modified_batch = {
            "playlists": [
                {
                    "name": "Favorites",
                    "playlist_name": "Favorites",
                    "has_changed": True,
                    "status": "created",
                    "version": 2,
                    "track_count": 26,
                    "track_diff": {"added_tracks": [{"title": "Added Song", "artist": "Band"}], "removed_tracks": []},
                    "unavailable_tracks_count": 0,
                }
            ],
            "total_alerts": 0,
        }
        res4 = service.send_digest_notification(modified_batch)
        assert res4["sent"] is True
        assert res4["template"] == "changes_digest"
        mock_send.assert_called_once()
        assert "Playlist Updates" in mock_send.call_args[0][0]


def test_cli_notify_commands(temp_config: ConfigManager, monkeypatch):
    monkeypatch.setitem(state, "config", temp_config)
    cli_runner = CliRunner()

    # 1. notify status before config
    res_stat1 = cli_runner.invoke(app, ["--json", "notify", "status"])
    assert res_stat1.exit_code == 0
    stat_data = json.loads(res_stat1.stdout)
    assert stat_data["configured"] is False

    # 2. notify config
    res_cfg = cli_runner.invoke(
        app,
        [
            "notify",
            "config",
            "--host",
            "smtp.custom.org",
            "--port",
            "465",
            "--ssl",
            "--to-addr",
            "user@test.org",
        ],
    )
    assert res_cfg.exit_code == 0
    assert "SMTP configuration updated" in res_cfg.stdout

    # 3. notify status after config
    res_stat2 = cli_runner.invoke(app, ["--json", "notify", "status"])
    assert res_stat2.exit_code == 0
    stat_data2 = json.loads(res_stat2.stdout)
    assert stat_data2["configured"] is True
    assert stat_data2["config"]["smtp_host"] == "smtp.custom.org"
    assert stat_data2["config"]["to_address"] == "user@test.org"

    # 4. notify test with mock SMTP
    with patch("smtplib.SMTP_SSL") as mock_ssl:
        mock_server = MagicMock()
        mock_ssl.return_value.__enter__.return_value = mock_server

        res_test = cli_runner.invoke(app, ["notify", "test"])
        assert res_test.exit_code == 0
        assert "Verification email sent to" in res_test.stdout


def test_cli_notify_configure_port_25_with_tls(temp_config: ConfigManager, monkeypatch):
    monkeypatch.setitem(state, "config", temp_config)
    cli_runner = CliRunner()

    res = cli_runner.invoke(
        app,
        [
            "notify",
            "config",
            "--host",
            "mail.myserver.org",
            "--port",
            "25",
            "--tls",
            "--to-addr",
            "xave@example.com",
            "--from-addr",
            "bot@example.com",
        ],
    )
    assert res.exit_code == 0
    cfg = temp_config.get_email_config()
    assert cfg.smtp_host == "mail.myserver.org"
    assert cfg.smtp_port == 25
    assert cfg.use_tls is True
    assert cfg.use_ssl is False
    assert cfg.to_address == "xave@example.com"
    assert cfg.from_address == "bot@example.com"
