"""Tests for JobRunner and jobs.yaml workflow execution."""

from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from curo.config import ConfigManager
from curo.core.provider import MusicProvider
from curo.services.job_runner import JobExecutionError, JobRunner


def test_list_jobs(tmp_path: Path):
    cfg = ConfigManager(custom_config_dir=tmp_path)
    runner = JobRunner(cfg=cfg)

    jobs = runner.list_jobs()
    assert "daily-discovery-megamix" in jobs


def test_run_unknown_job(tmp_path: Path):
    cfg = ConfigManager(custom_config_dir=tmp_path)
    runner = JobRunner(cfg=cfg)

    with pytest.raises(JobExecutionError, match="Job 'non-existent' not found"):
        runner.run_job("non-existent")


def test_run_job_success(tmp_path: Path):
    cfg = ConfigManager(custom_config_dir=tmp_path)
    mock_provider = MagicMock(spec=MusicProvider)
    mock_provider.name = "tidal"
    mock_provider.display_name = "Tidal"
    runner = JobRunner(provider=mock_provider, cfg=cfg)

    with patch("curo.services.job_runner.MixService") as MockMixService:
        mix_inst = MockMixService.return_value
        mix_inst.backup_daily_discovery.return_value = {"success": True, "tracks_added": 10}
        mix_inst.create_megamix.return_value = {"success": True, "tracks_added": 50}

        res = runner.run_job("daily-discovery-megamix")

        assert res["success"] is True
        assert res["actions_executed"] == 2
        assert mix_inst.backup_daily_discovery.call_count == 1
        assert mix_inst.create_megamix.call_count == 1
