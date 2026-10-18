"""Tests for ConfigManager and path management."""

from pathlib import Path
from tidal_cli.config import ConfigManager


def test_config_paths(tmp_path: Path):
    cfg = ConfigManager(custom_config_dir=tmp_path)
    assert cfg.config_dir == tmp_path
    assert cfg.session_file == tmp_path / "session.json"
    assert cfg.jobs_file == tmp_path / "jobs.yaml"
    assert cfg.log_file == tmp_path / "tidal-cli.log"


def test_ensure_jobs_file(tmp_path: Path):
    cfg = ConfigManager(custom_config_dir=tmp_path)
    assert not cfg.jobs_file.exists()

    created_path = cfg.ensure_jobs_file()
    assert created_path.exists()
    content = created_path.read_text(encoding="utf-8")
    assert "daily-discovery-megamix" in content
    assert "create_megamix" in content

