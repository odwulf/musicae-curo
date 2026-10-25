"""Configuration and path management for tidal-cli."""

from __future__ import annotations

import datetime
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field

DEFAULT_JOBS_YAML = """# tidal-cli automated job definitions
# Schedulable via cron: e.g. run at 06:00 UTC daily:
# 0 6 * * * /path/to/tidal-cli job run daily-discovery-megamix --quiet

jobs:
  daily-discovery-megamix:
    description: "Backup all daily mixes and build a randomized daily megamix"
    actions:
      - action: backup_daily_discovery
        params:
          playlist_name_format: "Discovery Backup - {date}"
          description: "Automated daily backup of Tidal Daily Discovery"
      - action: create_megamix
        params:
          target_playlist: "Daily Megamix"
          include_discovery: true
          include_daily_mixes: true
          shuffle: true
          mode: "replace" # 'replace' wipes previous megamix content; 'append' adds to it
          description: "All daily mix tunes in random order - updated on {date}"

  daily-playlist-backup:
    description: "Daily versioned snapshot of all personal playlists with availability alerts"
    actions:
      - action: snapshot_all_playlists
        params:
          alert_on_unavailable: true
          notify: true
"""


class AppSettings(BaseModel):
    """General application settings."""
    log_level: str = Field(default="INFO")
    timeout_seconds: int = Field(default=30)
    chunk_size: int = Field(default=50)  # max tracks per add_tracks API call


class EmailConfig(BaseModel):
    """SMTP email notification settings."""
    smtp_host: Optional[str] = Field(default=None, description="SMTP server host (e.g. smtp.gmail.com)")
    smtp_port: int = Field(default=587, description="SMTP server port (e.g. 587 for STARTTLS, 465 for SSL)")
    smtp_user: Optional[str] = Field(default=None, description="Username for SMTP authentication")
    smtp_password: Optional[str] = Field(default=None, description="Password for SMTP authentication")
    use_tls: bool = Field(default=True, description="Enable STARTTLS encryption")
    use_ssl: bool = Field(default=False, description="Enable direct SSL encryption")
    from_address: Optional[str] = Field(default=None, description="Sender email address")
    to_address: Optional[str] = Field(default=None, description="Recipient email address (or comma-separated list)")
    send_on_changes: bool = Field(default=True, description="Send email when a playlist modification is detected")
    send_on_alerts: bool = Field(default=True, description="Send email when an unavailable track alert is detected")

    @property
    def is_configured(self) -> bool:
        """Returns True if the minimum required SMTP fields are configured."""
        return bool(self.smtp_host and self.to_address)


class ConfigManager:
    """Manages paths, directory permissions, and configuration files."""

    def __init__(self, custom_config_dir: Optional[Path | str] = None) -> None:
        if custom_config_dir:
            self.config_dir = Path(custom_config_dir).expanduser().resolve()
        elif "TIDAL_CONFIG_DIR" in os.environ:
            self.config_dir = Path(os.environ["TIDAL_CONFIG_DIR"]).expanduser().resolve()
        else:
            self.config_dir = Path.home() / ".config" / "tidal-cli"

        self.session_file = self.config_dir / "session.json"
        self.config_file = self.config_dir / "config.json"
        self.jobs_file = self.config_dir / "jobs.yaml"
        self.log_file = self.config_dir / "tidal-cli.log"
        self.snapshots_dir = self.config_dir / "snapshots"
        self.track_registry_file = self.snapshots_dir / "index.json"
        self.logs_dir = self.config_dir / "logs"
        self.alerts_log_file = self.logs_dir / "availability_alerts.log"
        self.notification_state_file = self.config_dir / "notification_state.json"

    def ensure_config_dir(self) -> Path:
        """Create config directory with strict permissions on POSIX systems."""
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        if sys.platform != "win32":
            try:
                os.chmod(self.config_dir, 0o700)
            except OSError:
                pass
        return self.config_dir

    def secure_file(self, path: Path) -> None:
        """Apply strict 0600 file permissions on POSIX."""
        if sys.platform != "win32" and path.exists():
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass

    def ensure_jobs_file(self) -> Path:
        """Create a default jobs.yaml if it does not exist."""
        self.ensure_config_dir()
        if not self.jobs_file.exists():
            self.jobs_file.write_text(DEFAULT_JOBS_YAML, encoding="utf-8")
            self.secure_file(self.jobs_file)
        return self.jobs_file

    def get_email_config(self) -> EmailConfig:
        """Load email notification config from config.json and environment variables."""
        cfg_dict: Dict[str, Any] = {}
        if self.config_file.exists():
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    data = json.load(f) or {}
                    cfg_dict = data.get("email") or data.get("smtp") or {}
            except Exception:
                cfg_dict = {}

        # Environment variable overrides
        if "TIDAL_SMTP_HOST" in os.environ:
            cfg_dict["smtp_host"] = os.environ["TIDAL_SMTP_HOST"]
        if "TIDAL_SMTP_PORT" in os.environ:
            try:
                cfg_dict["smtp_port"] = int(os.environ["TIDAL_SMTP_PORT"])
            except ValueError:
                pass
        if "TIDAL_SMTP_USER" in os.environ:
            cfg_dict["smtp_user"] = os.environ["TIDAL_SMTP_USER"]
        if "TIDAL_SMTP_PASSWORD" in os.environ:
            cfg_dict["smtp_password"] = os.environ["TIDAL_SMTP_PASSWORD"]
        if "TIDAL_SMTP_TLS" in os.environ:
            cfg_dict["use_tls"] = os.environ["TIDAL_SMTP_TLS"].lower() in ("1", "true", "yes")
        if "TIDAL_SMTP_SSL" in os.environ:
            cfg_dict["use_ssl"] = os.environ["TIDAL_SMTP_SSL"].lower() in ("1", "true", "yes")
        if "TIDAL_EMAIL_FROM" in os.environ:
            cfg_dict["from_address"] = os.environ["TIDAL_EMAIL_FROM"]
        if "TIDAL_EMAIL_TO" in os.environ:
            cfg_dict["to_address"] = os.environ["TIDAL_EMAIL_TO"]

        return EmailConfig(**cfg_dict)

    def save_email_config(self, email_cfg: EmailConfig) -> None:
        """Persist email configuration to config.json with secure file permissions."""
        self.ensure_config_dir()
        existing_data: Dict[str, Any] = {}
        if self.config_file.exists():
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    existing_data = json.load(f) or {}
            except Exception:
                existing_data = {}

        existing_data["email"] = email_cfg.model_dump(exclude_none=True)
        with open(self.config_file, "w", encoding="utf-8") as f:
            json.dump(existing_data, f, indent=2, ensure_ascii=False)
        self.secure_file(self.config_file)

    def has_email_sent_before(self) -> bool:
        """Check whether a notification email has ever been sent previously."""
        if not self.notification_state_file.exists():
            return False
        try:
            with open(self.notification_state_file, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
                return bool(data.get("first_email_sent_at"))
        except Exception:
            return False

    def mark_email_sent(self, subject: str = "") -> None:
        """Record that an email was dispatched, preserving first_email_sent_at timestamp."""
        self.ensure_config_dir()
        data: Dict[str, Any] = {}
        if self.notification_state_file.exists():
            try:
                with open(self.notification_state_file, "r", encoding="utf-8") as f:
                    data = json.load(f) or {}
            except Exception:
                data = {}

        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        if not data.get("first_email_sent_at"):
            data["first_email_sent_at"] = now_iso
        data["last_email_sent_at"] = now_iso
        if subject:
            data["last_email_subject"] = subject

        with open(self.notification_state_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        self.secure_file(self.notification_state_file)


# Global default configuration manager instance
config_manager = ConfigManager()

