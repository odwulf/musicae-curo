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


def test_cli_job_run_quiet_and_json_modes(tmp_path: Path, monkeypatch):
    from typer.testing import CliRunner
    from curo.cli import app, state

    temp_cfg = ConfigManager(custom_config_dir=tmp_path)
    monkeypatch.setitem(state, "config", temp_cfg)

    mock_provider = MagicMock(spec=MusicProvider)
    mock_provider.name = "tidal"
    mock_provider.display_name = "Tidal"

    runner = CliRunner()

    with patch("curo.cli.get_active_provider", return_value=mock_provider), \
         patch("curo.services.job_runner.MixService") as MockMixService:
        mix_inst = MockMixService.return_value
        mix_inst.backup_daily_discovery.return_value = {"success": True, "tracks_added": 10}
        mix_inst.create_megamix.return_value = {"success": True, "tracks_added": 50}

        # 1. Normal run (without quiet)
        state["quiet"] = False
        state["json_mode"] = False
        res_normal = runner.invoke(app, ["job", "run", "daily-discovery-megamix"])
        assert res_normal.exit_code == 0
        assert "completed successfully" in res_normal.stdout

        # 2. Subcommand option --quiet
        state["quiet"] = False
        res_quiet = runner.invoke(app, ["job", "run", "daily-discovery-megamix", "--quiet"])
        assert res_quiet.exit_code == 0
        assert res_quiet.stdout == ""

        # 3. Subcommand option -q
        state["quiet"] = False
        res_q = runner.invoke(app, ["job", "run", "daily-discovery-megamix", "-q"])
        assert res_q.exit_code == 0
        assert res_q.stdout == ""

        # 4. Global root option --quiet
        state["quiet"] = False
        res_global_quiet = runner.invoke(app, ["--quiet", "job", "run", "daily-discovery-megamix"])
        assert res_global_quiet.exit_code == 0
        assert res_global_quiet.stdout == ""

        # 5. Subcommand option --json
        state["quiet"] = False
        state["json_mode"] = False
        res_json = runner.invoke(app, ["job", "run", "daily-discovery-megamix", "--json"])
        assert res_json.exit_code == 0
        assert '"actions_executed": 2' in res_json.stdout


def test_run_fresh_megamix_job(tmp_path: Path):
    """Verify daily-fresh-megamix job passes exclude_recent_days=7 and target_playlist."""
    cfg = ConfigManager(custom_config_dir=tmp_path)
    mock_provider = MagicMock(spec=MusicProvider)
    runner = JobRunner(provider=mock_provider, cfg=cfg)

    with patch("curo.services.job_runner.MixService") as MockMixService:
        mix_inst = MockMixService.return_value
        mix_inst.create_megamix.return_value = {"success": True, "tracks_added": 25}

        res = runner.run_job("daily-fresh-megamix")

        assert res["success"] is True
        assert res["actions_executed"] == 1
        mix_inst.create_megamix.assert_called_once_with(
            target_playlist="Daily Fresh Megamix",
            include_discovery=True,
            include_daily_mixes=True,
            shuffle=True,
            mode="replace",
            description="Fresh daily mix tunes not heard in the last 7 days - updated on {date}",
            limit=None,
            exclude_recent_days=7,
            folder=None,
        )


def test_run_job_action_failure_raises_error(tmp_path: Path):
    """Ensure JobRunner raises JobExecutionError when an action returns success=False."""
    cfg = ConfigManager(custom_config_dir=tmp_path)
    mock_provider = MagicMock(spec=MusicProvider)
    runner = JobRunner(provider=mock_provider, cfg=cfg)

    with patch("curo.services.job_runner.MixService") as MockMixService:
        mix_inst = MockMixService.return_value
        mix_inst.create_megamix.return_value = {
            "success": False,
            "reason": "All candidate tracks were excluded by the 7-day history filter",
        }

        with pytest.raises(JobExecutionError, match="All candidate tracks were excluded"):
            runner.run_job("daily-fresh-megamix")


def test_job_runner_passes_job_level_and_action_level_folder(tmp_path: Path):
    """Verify folder setting at job level and action params level is passed correctly."""
    cfg = ConfigManager(custom_config_dir=tmp_path)
    custom_yaml = """
jobs:
  folder-job:
    folder: "Default Backup Folder"
    actions:
      - action: backup_daily_discovery
        params:
          playlist_name_format: "Discovery - {date}"
      - action: create_megamix
        params:
          target_playlist: "Custom Megamix"
          folder: "Explicit Megamix Folder"
"""
    cfg.jobs_file.write_text(custom_yaml, encoding="utf-8")
    mock_provider = MagicMock(spec=MusicProvider)
    runner = JobRunner(provider=mock_provider, cfg=cfg)

    with patch("curo.services.job_runner.MixService") as MockMixService:
        mix_inst = MockMixService.return_value
        mix_inst.backup_daily_discovery.return_value = {"success": True, "tracks_added": 10}
        mix_inst.create_megamix.return_value = {"success": True, "total_unique_tracks": 50}

        res = runner.run_job("folder-job")
        assert res["success"] is True
        assert res["actions_executed"] == 2

        # Action 1 should inherit job-level folder
        call1 = mix_inst.backup_daily_discovery.call_args[1]
        assert call1["folder"] == "Default Backup Folder"

        # Action 2 should use its own overriding action folder
        call2 = mix_inst.create_megamix.call_args[1]
        assert call2["folder"] == "Explicit Megamix Folder"



def test_job_runner_send_daily_digest_action(tmp_path: Path):
    cfg = ConfigManager(custom_config_dir=tmp_path)
    custom_yaml = """
jobs:
  daily-digest-job:
    actions:
      - action: send_daily_digest
        params:
          force: true
          include_followers: true
"""
    cfg.jobs_file.write_text(custom_yaml, encoding="utf-8")
    mock_provider = MagicMock(spec=MusicProvider)
    runner = JobRunner(provider=mock_provider, cfg=cfg)

    with patch("curo.services.job_runner.SnapshotService") as MockSnapshotService:
        snap_inst = MockSnapshotService.return_value
        snap_inst.send_daily_digest.return_value = {"sent": True, "success": True}

        res = runner.run_job("daily-digest-job")
        assert res["success"] is True
        assert res["actions_executed"] == 1
        snap_inst.send_daily_digest.assert_called_once_with(
            force=True,
            include_followers=True,
        )
