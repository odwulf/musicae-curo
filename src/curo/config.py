"""Configuration and path management for Musicae Curo (curo)."""

from __future__ import annotations

import datetime
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field

DEFAULT_JOBS_YAML = """# Musicae Curo (curo) automated job definitions
# Schedulable via cron: e.g. run daily at 06:00 UTC:
# 0 6 * * * /path/to/curo job run daily-discovery-megamix --quiet
#
# Note: You can specify a destination playlist 'folder' (e.g. 'Discovery Backups')
# either at the job level (default for actions in the job) or per action under 'params:'.

jobs:
  daily-discovery-megamix:
    description: "Backup all daily mixes and build a randomized daily megamix"
    actions:
      - action: backup_daily_discovery
        params:
          playlist_name_format: "Discovery Backup - {date}"
          description: "Automated daily backup of Daily Discovery"
          folder: "Discovery Backups"
      - action: create_megamix
        params:
          target_playlist: "Daily Megamix"
          include_discovery: true
          include_daily_mixes: true
          shuffle: true
          mode: "replace" # 'replace' wipes previous megamix content; 'append' adds to it
          exclude_recent_days: 0
          description: "All daily mix tunes in random order - updated on {date}"

  daily-fresh-megamix:
    description: "Build a daily megamix containing only fresh tracks not seen in the last 7 days"
    actions:
      - action: create_megamix
        params:
          target_playlist: "Daily Fresh Megamix"
          include_discovery: true
          include_daily_mixes: true
          shuffle: true
          mode: "replace"
          exclude_recent_days: 7 # only include tunes not featured in the preceding 7 days
          description: "Fresh daily mix tunes not heard in the last 7 days - updated on {date}"

  daily-playlist-backup:
    description: "Daily versioned snapshot of all personal playlists with availability alerts"
    actions:
      - action: snapshot_all_playlists
        params:
          alert_on_unavailable: true
          notify: false

  daily-favorites-backup:
    description: "Daily versioned snapshot and playlist mirror of all favorite/liked tracks"
    actions:
      - action: snapshot_favorite_tracks
        params:
          alert_on_unavailable: true
      - action: backup_favorites_to_playlist
        params:
          target_playlist: "Favorites Backup"
          mode: "replace"
          description: "Automated daily backup of favorite/liked tracks"

  daily-digest:
    description: "Dispatch unified daily email digest with playlist changes, availability alerts, and follower reach"
    actions:
      - action: send_daily_digest
        params:
          send_when_no_changes: false
          include_followers: true
"""


class AppSettings(BaseModel):
    """General application settings."""
    log_level: str = Field(default="INFO")
    timeout_seconds: int = Field(default=30)
    chunk_size: int = Field(default=50)


class EmailConfig(BaseModel):
    """SMTP email notification settings."""
    smtp_host: Optional[str] = Field(default=None, description="SMTP server host (e.g. smtp.gmail.com)")
    smtp_port: int = Field(default=587, description="SMTP server port (e.g. 587 for STARTTLS, 465 for SSL, 25 for local TLS)")
    smtp_user: Optional[str] = Field(default=None, description="Username for SMTP authentication")
    smtp_password: Optional[str] = Field(default=None, description="Password for SMTP authentication")
    use_tls: bool = Field(default=True, description="Enable STARTTLS encryption")
    use_ssl: bool = Field(default=False, description="Enable direct SSL encryption")
    from_address: Optional[str] = Field(default=None, description="Sender email address")
    to_address: Optional[str] = Field(default=None, description="Recipient email address (or comma-separated list)")
    send_on_changes: bool = Field(default=True, description="Send email when a playlist modification is detected")
    send_on_alerts: bool = Field(default=True, description="Send email when an unavailable track alert is detected")
    exclude_megamix_diff: bool = Field(default=True, description="Exclude exhaustive track additions/removals for megamix playlists")

    @property
    def is_configured(self) -> bool:
        """Returns True if minimum required SMTP fields are configured."""
        return bool(self.smtp_host and self.to_address)


class ConfigManager:
    """Manages paths, directory permissions, provider configurations, and settings."""

    def __init__(self, custom_config_dir: Optional[Path | str] = None) -> None:
        if custom_config_dir:
            self.config_dir = Path(custom_config_dir).expanduser().resolve()
        elif "CURO_CONFIG_DIR" in os.environ:
            self.config_dir = Path(os.environ["CURO_CONFIG_DIR"]).expanduser().resolve()
        else:
            self.config_dir = Path.home() / ".config" / "curo"

        self.config_file = self.config_dir / "config.json"
        self.jobs_file = self.config_dir / "jobs.yaml"
        self.log_file = self.config_dir / "curo.log"
        self.snapshots_dir = self.config_dir / "snapshots"
        self.track_registry_file = self.snapshots_dir / "index.json"
        self.logs_dir = self.config_dir / "logs"
        self.alerts_log_file = self.logs_dir / "availability_alerts.log"
        self.notification_state_file = self.config_dir / "notification_state.json"
        self.megamix_history_file = self.config_dir / "megamix_history.json"

    def get_provider_session_file(self, provider_name: str) -> Path:
        """Return the path to the session credentials file for a specific provider."""
        return self.config_dir / f"{provider_name.lower()}_session.json"

    def get_provider_snapshots_dir(self, provider_name: str) -> Path:
        """Return the snapshot storage directory for a specific provider."""
        p_dir = self.snapshots_dir / provider_name.lower()
        p_dir.mkdir(parents=True, exist_ok=True)
        return p_dir

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
        """Create default jobs.yaml if it does not exist."""
        self.ensure_config_dir()
        if not self.jobs_file.exists():
            self.jobs_file.write_text(DEFAULT_JOBS_YAML, encoding="utf-8")
            self.secure_file(self.jobs_file)
        return self.jobs_file

    def get_default_provider(self) -> str:
        """Return the default active provider name."""
        if "CURO_PROVIDER" in os.environ:
            return os.environ["CURO_PROVIDER"].lower()
        if self.config_file.exists():
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    data = json.load(f) or {}
                    if data.get("default_provider"):
                        return data["default_provider"].lower()
            except Exception:
                pass
        return "tidal"

    def set_default_provider(self, provider_name: str) -> None:
        """Set and persist the default active provider name."""
        self.ensure_config_dir()
        data = self.load_raw_config()
        data["default_provider"] = provider_name.lower()
        self.save_raw_config(data)

    def load_raw_config(self) -> Dict[str, Any]:
        """Load raw json config dictionary."""
        if self.config_file.exists():
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    return json.load(f) or {}
            except Exception:
                return {}
        return {}

    def save_raw_config(self, data: Dict[str, Any]) -> None:
        """Save raw json config dictionary securely."""
        self.ensure_config_dir()
        with open(self.config_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        self.secure_file(self.config_file)

    def get_email_config(self) -> EmailConfig:
        """Load email notification config from config.json and environment variables."""
        cfg_dict: Dict[str, Any] = {}
        raw = self.load_raw_config()
        cfg_dict = raw.get("email") or raw.get("smtp") or {}

        # Environment variable overrides (CURO_SMTP_* or legacy fallback)
        for env_prefix in ("CURO_", "TIDAL_"):
            if f"{env_prefix}SMTP_HOST" in os.environ:
                cfg_dict["smtp_host"] = os.environ[f"{env_prefix}SMTP_HOST"]
            if f"{env_prefix}SMTP_PORT" in os.environ:
                try:
                    cfg_dict["smtp_port"] = int(os.environ[f"{env_prefix}SMTP_PORT"])
                except ValueError:
                    pass
            if f"{env_prefix}SMTP_USER" in os.environ:
                cfg_dict["smtp_user"] = os.environ[f"{env_prefix}SMTP_USER"]
            if f"{env_prefix}SMTP_PASSWORD" in os.environ:
                cfg_dict["smtp_password"] = os.environ[f"{env_prefix}SMTP_PASSWORD"]
            if f"{env_prefix}SMTP_TLS" in os.environ:
                cfg_dict["use_tls"] = os.environ[f"{env_prefix}SMTP_TLS"].lower() in ("1", "true", "yes")
            if f"{env_prefix}SMTP_SSL" in os.environ:
                cfg_dict["use_ssl"] = os.environ[f"{env_prefix}SMTP_SSL"].lower() in ("1", "true", "yes")
            if f"{env_prefix}EMAIL_FROM" in os.environ:
                cfg_dict["from_address"] = os.environ[f"{env_prefix}EMAIL_FROM"]
            if f"{env_prefix}EMAIL_TO" in os.environ:
                cfg_dict["to_address"] = os.environ[f"{env_prefix}EMAIL_TO"]
            if f"{env_prefix}EXCLUDE_MEGAMIX_DIFF" in os.environ:
                cfg_dict["exclude_megamix_diff"] = os.environ[f"{env_prefix}EXCLUDE_MEGAMIX_DIFF"].lower() in ("1", "true", "yes")

        return EmailConfig(**cfg_dict)

    def save_email_config(self, email_cfg: EmailConfig) -> None:
        """Persist email configuration to config.json with secure file permissions."""
        data = self.load_raw_config()
        data["email"] = email_cfg.model_dump(exclude_none=True)
        self.save_raw_config(data)

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

