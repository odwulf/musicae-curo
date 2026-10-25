"""Configuration and path management for tidal-cli."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional
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
"""


class AppSettings(BaseModel):
    """General application settings."""
    log_level: str = Field(default="INFO")
    timeout_seconds: int = Field(default=30)
    chunk_size: int = Field(default=50)  # max tracks per add_tracks API call


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
        self.jobs_file = self.config_dir / "jobs.yaml"
        self.log_file = self.config_dir / "tidal-cli.log"

    def ensure_config_dir(self) -> Path:
        """Create config directory with strict permissions on POSIX systems."""
        self.config_dir.mkdir(parents=True, exist_ok=True)
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


# Global default configuration manager instance
config_manager = ConfigManager()

