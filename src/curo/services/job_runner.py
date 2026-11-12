"""Job runner for executing headless automated tasks defined in jobs.yaml."""

from __future__ import annotations

import json
import logging
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional
import yaml

from curo.config import ConfigManager, config_manager
from curo.core.provider import MusicProvider
from curo.core.registry import ProviderRegistry
from curo.services.mix_service import MixService
from curo.services.playlist_service import PlaylistService
from curo.services.snapshot_service import SnapshotService

logger = logging.getLogger(__name__)


class JobExecutionError(Exception):
    """Raised when an automated job action fails."""
    pass


class JobRunner:
    """Executes scheduled jobs from jobs.yaml."""

    def __init__(
        self,
        provider: Optional[MusicProvider] = None,
        cfg: Optional[ConfigManager] = None,
    ) -> None:
        self.config = cfg or config_manager
        self._provider = provider

    @property
    def provider(self) -> MusicProvider:
        """Resolve active provider lazily."""
        if self._provider is None:
            provider_name = self.config.get_default_provider()
            self._provider = ProviderRegistry.create_provider(provider_name, cfg=self.config)
        return self._provider

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

        playlist_service = PlaylistService(self.provider, cfg=self.config)
        mix_service = MixService(self.provider)
        snapshot_service = SnapshotService(self.provider, cfg=self.config)

        action_results: List[Dict[str, Any]] = []

        job_folder = job_spec.get("folder")

        for idx, act in enumerate(actions, start=1):
            action_type = act.get("action")
            params = act.get("params", {}) or {}
            folder = params.get("folder") or job_folder

            logger.info(f"Running action {idx}/{len(actions)}: {action_type} with params={params}")

            try:
                if action_type == "sync_mixes":
                    res = mix_service.sync_mixes(
                        folder=folder,
                        backup_discovery=params.get("backup_discovery", True),
                        update_megamixes=params.get("update_megamixes", True),
                    )
                elif action_type == "backup_daily_discovery":
                    res = mix_service.backup_daily_discovery(
                        playlist_name_format=params.get("playlist_name_format", "Discovery Backup - {date}"),
                        description=params.get("description", f"Automated daily backup of {self.provider.display_name} Daily Discovery"),
                        folder=folder,
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
                        exclude_recent_days=params.get("exclude_recent_days", 0),
                        folder=folder,
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
                        alert_on_unavailable=params.get("alert_on_unavailable", True),
                        notify=params.get("notify", True),
                        force_all_mail=params.get("force_all_mail", False),
                    )
                elif action_type in ("send_daily_digest", "send_digest", "notify_digest"):
                    send_when_no_changes = bool(
                        params.get("send_when_no_changes", params.get("force", params.get("force_all", False)))
                    )
                    res = snapshot_service.send_daily_digest(
                        send_when_no_changes=send_when_no_changes,
                        force=send_when_no_changes,
                        include_followers=params.get("include_followers", True),
                    )
                elif action_type == "snapshot_playlist":
                    res = snapshot_service.snapshot_playlist(
                        identifier=params["playlist"],
                        alert_on_unavailable=params.get("alert_on_unavailable", True),
                    )
                elif action_type == "snapshot_favorite_tracks":
                    res = snapshot_service.snapshot_favorite_tracks(
                        alert_on_unavailable=params.get("alert_on_unavailable", True),
                    )
                elif action_type == "backup_favorites_to_playlist":
                    res = playlist_service.backup_favorites_to_playlist(
                        target_playlist=params.get("target_playlist", "Favorites Backup"),
                        mode=params.get("mode", "replace"),
                        description=params.get("description", "Automated backup of favorite/liked tracks"),
                        folder=folder,
                    )
                elif action_type == "sync_playlist":
                    from curo.services.playlist_sync_service import PlaylistSyncService

                    src_p_name = params.get("source_provider") or self.provider.name
                    tgt_p_name = params.get("target_provider") or src_p_name

                    src_p = (
                        self.provider
                        if src_p_name == self.provider.name
                        else ProviderRegistry.create_provider(src_p_name, cfg=self.config)
                    )
                    tgt_p = (
                        src_p
                        if tgt_p_name == src_p_name
                        else ProviderRegistry.create_provider(tgt_p_name, cfg=self.config)
                    )

                    sync_svc = PlaylistSyncService(source_provider=src_p, target_provider=tgt_p, cfg=self.config)
                    raw_thresh = params.get("threshold", 80.0)
                    thresh_ratio = float(raw_thresh) / 100.0 if float(raw_thresh) > 1.0 else float(raw_thresh)
                    res = sync_svc.sync_playlists(
                        source_identifier=params["source_playlist"],
                        target_identifier=params.get("target_playlist"),
                        mode=params.get("mode", "mirror"),
                        threshold=thresh_ratio,
                        dry_run=params.get("dry_run", False),
                        create_if_missing=params.get("create_if_missing", True),
                        folder=folder,
                    )
                elif action_type in ("webhook", "call_webhook", "http_webhook"):
                    url = params.get("url")
                    if not url:
                        raise JobExecutionError("Webhook action requires 'url' parameter.")

                    only_if_changed = bool(params.get("only_if_changed", False))
                    if only_if_changed:
                        # Check whether any previous action indicated changes
                        has_changes = False
                        for prev in action_results:
                            prev_res = prev.get("result")
                            if isinstance(prev_res, dict):
                                # sync_mixes result check
                                if prev_res.get("mixes_updated") or prev_res.get("megamixes_updated") or prev_res.get("discovery_backup"):
                                    has_changes = True
                                    break
                                # playlist/snapshot/backup modification checks
                                if prev_res.get("tracks_added", 0) > 0 or prev_res.get("has_changes") or prev_res.get("modified_playlists"):
                                    has_changes = True
                                    break
                                if prev_res.get("status") == "updated" or prev_res.get("has_changed"):
                                    has_changes = True
                                    break

                        if not has_changes:
                            logger.info(f"Skipping webhook to {url}: only_if_changed=True and no previous actions detected changes.")
                            res = {"skipped": True, "reason": "no_changes_detected", "url": url}
                            action_results.append({"action": action_type, "status": "skipped", "result": res})
                            continue

                    method = str(params.get("method", "POST")).upper()
                    headers = dict(params.get("headers", {}) or {})
                    timeout = int(params.get("timeout", 15))

                    last_res = action_results[-1].get("result") if action_results else {}
                    custom_payload = params.get("payload") or params.get("data")
                    if custom_payload is not None:
                        body_data = custom_payload
                    else:
                        body_data = {
                            "event": "curo_job_action",
                            "job": job_name,
                            "provider": self.provider.name,
                            "last_action_result": last_res,
                        }

                    data_bytes = None
                    if method in ("POST", "PUT", "PATCH"):
                        if isinstance(body_data, (dict, list)):
                            headers.setdefault("Content-Type", "application/json")
                            data_bytes = json.dumps(body_data, default=str).encode("utf-8")
                        elif isinstance(body_data, str):
                            data_bytes = body_data.encode("utf-8")

                    req = urllib.request.Request(url, data=data_bytes, headers=headers, method=method)
                    try:
                        with urllib.request.urlopen(req, timeout=timeout) as resp:
                            resp_code = resp.getcode()
                            resp_body = resp.read().decode("utf-8", errors="replace")
                        logger.info(f"Webhook {method} {url} returned HTTP {resp_code}")
                        res = {"success": True, "status_code": resp_code, "response": resp_body[:500], "url": url}
                    except urllib.error.HTTPError as he:
                        err_body = he.read().decode("utf-8", errors="replace") if hasattr(he, "read") else ""
                        logger.warning(f"Webhook {method} {url} failed with HTTP {he.code}: {err_body[:200]}")
                        res = {"success": False, "status_code": he.code, "reason": f"HTTP {he.code}: {he.reason}", "url": url}
                    except Exception as we:
                        logger.warning(f"Webhook {method} {url} failed: {we}")
                        res = {"success": False, "reason": str(we), "url": url}
                else:
                    raise JobExecutionError(f"Unknown action type: {action_type}")

                if isinstance(res, dict) and res.get("success") is False:
                    reason = res.get("reason") or "Action indicated failure"
                    raise JobExecutionError(f"Action '{action_type}' in job '{job_name}' failed: {reason}")

                action_results.append({"action": action_type, "status": "success", "result": res})
            except Exception as e:
                logger.error(f"Action '{action_type}' failed: {e}", exc_info=True)
                raise JobExecutionError(f"Action '{action_type}' in job '{job_name}' failed: {e}")

        return {
            "job": job_name,
            "provider": self.provider.name,
            "success": True,
            "actions_executed": len(action_results),
            "results": action_results,
        }

    def generate_cron_help(self, job_name: str = "daily-discovery-megamix") -> str:
        """Generate ready-to-copy crontab lines."""
        py_exec = sys.executable
        return (
            f"# Run Musicae Curo job '{job_name}' daily at 06:00 UTC\n"
            f"0 6 * * * {py_exec} -m curo.cli job run {job_name} --quiet >> ~/.config/curo/cron.log 2>&1\n"
        )

