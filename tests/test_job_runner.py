"""Tests for JobRunner and jobs.yaml workflow execution."""

from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from tidal_cli.config import ConfigManager
from tidal_cli.services.job_runner import JobExecutionError, JobRunner


def test_list_jobs(tmp_path: Path):
    cfg = ConfigManager(custom_config_dir=tmp_path)
    runner = JobRunner(cfg)

    jobs = runner.list_jobs()
    assert "daily-discovery-megamix" in jobs


def test_run_unknown_job(tmp_path: Path):
    cfg = ConfigManager(custom_config_dir=tmp_path)
    runner = JobRunner(cfg)

    with pytest.raises(JobExecutionError, match="Job 'non-existent' not found"):
        runner.run_job("non-existent")


def test_run_job_success(tmp_path: Path):
    cfg = ConfigManager(custom_config_dir=tmp_path)
    runner = JobRunner(cfg)

    with patch.object(runner.session_manager, "load_session") as mock_load:
        mock_session = MagicMock()
        mock_load.return_value = mock_session

        with patch("tidal_cli.services.job_runner.MixService") as MockMixService:
            mix_inst = MockMixService.return_value
            mix_inst.backup_daily_discovery.return_value = {"success": True, "tracks_added": 10}
            mix_inst.create_megamix.return_value = {"success": True, "tracks_added": 50}

            res = runner.run_job("daily-discovery-megamix")

            assert res["success"] is True
            assert res["actions_executed"] == 2
            assert mix_inst.backup_daily_discovery.call_count == 1
            assert mix_inst.create_megamix.call_count == 1

