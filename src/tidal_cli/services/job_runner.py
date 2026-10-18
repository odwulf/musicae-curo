"""Job runner for executing headless automated tasks defined in jobs.yaml."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional
import yaml

from tidal_cli.auth import SessionManager
from tidal_cli.config import ConfigManager, config_manager
from tidal_cli.services.mix_service import MixService
from tidal_cli.services.playlist_service import PlaylistService
from tidal_cli.services.snapshot_service import SnapshotService

logger = logging.getLogger("tidal_cli.job_runner")


class JobExecutionError(Exception):
    """Raised when an automated job action fails."""
    pass


class JobRunner:
    """Executes scheduled jobs from jobs.yaml."""

    def __init__(self, cfg: Optional[ConfigManager] = None) -> None:
        self.config = cfg or config_manager
        self.session_manager = SessionManager(self.config)

    def load_jobs_definition(self) -> Dict[str, Any]:
        """Load jobs from jobs.yaml, creating default if not found."""
        jobs_file = self.config.ensure_jobs_file()
        try:
            with open(jobs_file, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
                return data.get("jobs", {})
        except Exception as e:
            raise JobExecutionError(f"Failed to parse jobs file at {jobs_file}: {e}")

    def list_jobs(self) -> Dict[str, str]:
        """List all configured job names and their descriptions."""
        jobs = self.load_jobs_definition()
        return {name: spec.get("description", "No description") for name, spec in jobs.items()}

    def run_job(self, job_name: str) -> Dict[str, Any]:
        """Execute a declared job by name."""
        jobs = self.load_jobs_definition()
        if job_name not in jobs:
            available = list(jobs.keys())
            raise JobExecutionError(
                f"Job '{job_name}' not found in jobs.yaml. Available jobs: {available}"
            )

        job_spec = jobs[job_name]
        actions: List[Dict[str, Any]] = job_spec.get("actions", [])
        if not actions:
            logger.warning(f"Job '{job_name}' has no actions configured.")
            return {"job": job_name, "success": True, "actions_executed": 0}

        # Ensure session is active and valid (silent refresh if expired)
        session = self.session_manager.load_session(auto_refresh=True)
        playlist_service = PlaylistService(session)
        mix_service = MixService(session)
        snapshot_service = SnapshotService(session, cfg=self.config)

        action_results: List[Dict[str, Any]] = []

        for idx, act in enumerate(actions, start=1):
            action_type = act.get("action")
            params = act.get("params", {}) or {}

            logger.info(f"Running action {idx}/{len(actions)}: {action_type} with params={params}")

            try:
                if action_type == "backup_daily_discovery":
                    res = mix_service.backup_daily_discovery(
                        playlist_name_format=params.get("playlist_name_format", "Discovery Backup - {date}"),
                        description=params.get("description", "Automated daily backup of Tidal Daily Discovery"),
                    )
                elif action_type == "create_megamix":
                    res = mix_service.create_megamix(
                        target_playlist=params.get("target_playlist", "Daily Megamix"),
                        include_discovery=params.get("include_discovery", True),
                        include_daily_mixes=params.get("include_daily_mixes", True),
                        shuffle=params.get("shuffle", True),
                        mode=params.get("mode", "replace"),
                        description=params.get("description", "All daily mix tunes in random order - updated on {date}"),
                        limit=params.get("limit"),
                    )
                elif action_type == "export_playlist":
                    res = {
                        "exported": playlist_service.export_playlist(
                            identifier=params["playlist"],
                            export_format=params.get("format", "json"),
                        )
                    }
                elif action_type == "snapshot_all_playlists":
                    res = snapshot_service.snapshot_all_playlists(
                        alert_on_unavailable=params.get("alert_on_unavailable", True)
                    )
                elif action_type == "snapshot_playlist":
                    res = snapshot_service.snapshot_playlist(
                        identifier=params["playlist"],
                        alert_on_unavailable=params.get("alert_on_unavailable", True),
                    )
                else:
                    raise JobExecutionError(f"Unknown action type: {action_type}")

                action_results.append({"action": action_type, "status": "success", "result": res})
            except Exception as e:
                logger.error(f"Action '{action_type}' failed: {e}", exc_info=True)
                raise JobExecutionError(f"Action '{action_type}' in job '{job_name}' failed: {e}")

        return {
            "job": job_name,
            "success": True,
            "actions_executed": len(action_results),
            "results": action_results,
        }

    def generate_cron_help(self, job_name: str = "daily-discovery-megamix") -> str:
        """Generate ready-to-copy crontab lines."""
        # Find path to python executable or tidal-cli entrypoint
        py_exec = sys.executable
        return (
            f"# Run Tidal Daily Discovery Backup & Megamix daily at 06:00 UTC\n"
            f"0 6 * * * {py_exec} -m tidal_cli.cli job run {job_name} --quiet >> ~/.config/tidal-cli/cron.log 2>&1\n"
        )

