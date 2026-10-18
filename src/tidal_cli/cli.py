"""CLI interface for tidal-cli built with Typer and Rich."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from tidal_cli.auth import AuthenticationError, SessionManager
from tidal_cli.config import ConfigManager, config_manager
from tidal_cli.services.album_service import AlbumService
from tidal_cli.services.job_runner import JobExecutionError, JobRunner
from tidal_cli.services.mix_service import MixService
from tidal_cli.services.playlist_service import PlaylistService
from tidal_cli.services.snapshot_service import SnapshotService

app = typer.Typer(
    name="tidal-cli",
    help="Schedulable CLI client for TIDAL playlist management and automated daily mixes.",
    add_completion=False,
)

auth_app = typer.Typer(name="auth", help="Manage TIDAL authentication and sessions.")
playlist_app = typer.Typer(name="playlist", help="Inspect, create, and export playlists with metadata.")
album_app = typer.Typer(name="album", help="Inspect and export albums with full metadata.")
mix_app = typer.Typer(name="mix", help="Daily discovery archiving and randomized megamix routines.")
job_app = typer.Typer(name="job", help="Manage and execute automated cron jobs.")
notify_app = typer.Typer(name="notify", help="Configure and dispatch email digests and availability notifications.")

app.add_typer(auth_app)
app.add_typer(playlist_app)
app.add_typer(album_app)
app.add_typer(mix_app)
app.add_typer(job_app)
app.add_typer(notify_app)

console = Console()
err_console = Console(stderr=True)


def setup_logging(verbose: bool, log_file: Optional[Path] = None) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    handlers: list[logging.Handler] = []
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=handlers if handlers else [logging.NullHandler()],
    )


# ---------------------------------------------------------------------------
# Global State
# ---------------------------------------------------------------------------
state = {
    "config": config_manager,
    "quiet": False,
    "json_mode": False,
}


@app.callback()
def main(
    config: Optional[Path] = typer.Option(
        None, "--config", "-c", help="Path to config directory [default: ~/.config/tidal-cli]"
    ),
    quiet: bool = typer.Option(
        False, "--quiet", "-q", help="Suppress progress bars and banners (ideal for cron)."
    ),
    json_mode: bool = typer.Option(
        False, "--json", help="Output machine-readable JSON."
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Enable verbose debug logging."
    ),
) -> None:
    if config:
        state["config"] = ConfigManager(config)
    state["quiet"] = quiet
    state["json_mode"] = json_mode
    setup_logging(verbose=verbose, log_file=state["config"].log_file)


# ---------------------------------------------------------------------------
# Auth Commands
# ---------------------------------------------------------------------------
@auth_app.command("login")
def auth_login() -> None:
    """Log in interactively using Tidal's OAuth2 Device Authorization flow."""
    mgr = SessionManager(state["config"])
    try:
        mgr.login_interactive()
    except Exception as e:
        err_console.print(f"[bold red]Login failed:[/bold red] {e}")
        raise typer.Exit(code=1)


@auth_app.command("status")
def auth_status() -> None:
    """Display active session status and user information."""
    mgr = SessionManager(state["config"])
    status = mgr.get_status()

    if state["json_mode"]:
        console.print_json(data=status)
        return

    if status.get("logged_in"):
        table = Table(title="TIDAL Session Status", show_header=False, border_style="green")
        table.add_row("Status", "[bold green]Authenticated[/bold green]")
        table.add_row("User", str(status.get("user_name")))
        table.add_row("User ID", str(status.get("user_id")))
        table.add_row("Country", str(status.get("country_code")))
        table.add_row("Session File", str(status.get("session_file")))
        console.print(table)
    else:
        err_console.print(
            f"[bold red]Not Authenticated[/bold red]: {status.get('error', 'No active session')}\n"
            f"[dim]Run 'tidal-cli auth login' to connect your account.[/dim]"
        )
        raise typer.Exit(code=1)


@auth_app.command("logout")
def auth_logout() -> None:
    """Log out and remove local credentials."""
    mgr = SessionManager(state["config"])
    if mgr.logout():
        console.print("[green]Logged out successfully. Stored session removed.[/green]")
    else:
        console.print("[dim]No active session file found.[/dim]")


# ---------------------------------------------------------------------------
# Playlist Commands
# ---------------------------------------------------------------------------
@playlist_app.command("list")
def playlist_list() -> None:
    """List all playlists owned by the authenticated user."""
    mgr = SessionManager(state["config"])
    try:
        session = mgr.load_session()
        svc = PlaylistService(session)
        playlists = svc.list_playlists()
    except AuthenticationError as e:
        err_console.print(f"[bold red]Auth Error:[/bold red] {e}")
        raise typer.Exit(code=1)

    if state["json_mode"]:
        console.print_json(data=playlists)
        return

    table = Table(title="Your Playlists", border_style="cyan")
    table.add_column("Name", style="bold")
    table.add_column("Tracks", justify="right")
    table.add_column("Duration (min)", justify="right")
    table.add_column("Public", justify="center")
    table.add_column("ID", style="dim")

    for pl in playlists:
        mins = round(pl["duration_seconds"] / 60, 1)
        table.add_row(
            pl["name"],
            str(pl["num_tracks"]),
            str(mins),
            "Yes" if pl["public"] else "No",
            pl["id"],
        )

    console.print(table)


@playlist_app.command("show")
def playlist_show(
    identifier: str = typer.Argument(..., help="Playlist ID or Name")
) -> None:
    """Display tracks and details for a playlist."""
    mgr = SessionManager(state["config"])
    try:
        session = mgr.load_session()
        svc = PlaylistService(session)
        pl = svc.get_playlist(identifier)
        if not pl:
            err_console.print(f"[bold red]Playlist not found:[/bold red] {identifier}")
            raise typer.Exit(code=1)
        tracks = pl.tracks()
    except Exception as e:
        err_console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(code=1)

    if state["json_mode"]:
        data = {
            "id": pl.id,
            "name": pl.name,
            "tracks": [{"id": t.id, "title": t.name, "artist": getattr(getattr(t, "artist", None), "name", "")} for t in tracks],
        }
        console.print_json(data=data)
        return

    table = Table(title=f"Playlist: {pl.name} ({len(tracks)} tracks)", border_style="blue")
    table.add_column("#", justify="right", style="dim")
    table.add_column("Title", style="bold")
    table.add_column("Artist")
    table.add_column("Album", style="dim")
    table.add_column("Duration", justify="right")

    for idx, t in enumerate(tracks, 1):
        artist = getattr(getattr(t, "artist", None), "name", "Unknown")
        album = getattr(getattr(t, "album", None), "name", "Unknown")
        dur_secs = getattr(t, "duration", 0)
        dur_str = f"{dur_secs // 60}:{dur_secs % 60:02d}"
        table.add_row(str(idx), t.name, artist, album, dur_str)

    console.print(table)


@playlist_app.command("create")
def playlist_create(
    title: str = typer.Argument(..., help="Title of the new playlist"),
    desc: str = typer.Option("", "--desc", "-d", help="Optional description"),
    public: bool = typer.Option(False, "--public", help="Make playlist public"),
) -> None:
    """Create a new playlist."""
    mgr = SessionManager(state["config"])
    try:
        session = mgr.load_session()
        svc = PlaylistService(session)
        pl = svc.create_playlist(title=title, description=desc, public=public)
    except Exception as e:
        err_console.print(f"[bold red]Failed to create playlist:[/bold red] {e}")
        raise typer.Exit(code=1)

    if state["json_mode"]:
        console.print_json(data={"id": pl.id, "name": pl.name})
    else:
        console.print(f"[green]Created playlist '[bold]{pl.name}[/bold]' (ID: {pl.id})[/green]")


@playlist_app.command("export")
def playlist_export(
    identifier: str = typer.Argument(..., help="Playlist ID or Name"),
    format: str = typer.Option("json", "--format", "-f", help="Format: json, m3u8, or csv"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="File to write export to"),
) -> None:
    """Export a playlist to JSON, M3U8, or CSV."""
    mgr = SessionManager(state["config"])
    try:
        session = mgr.load_session()
        svc = PlaylistService(session)
        content = svc.export_playlist(identifier, export_format=format)
    except Exception as e:
        err_console.print(f"[bold red]Export failed:[/bold red] {e}")
        raise typer.Exit(code=1)

    if output:
        output.write_text(content, encoding="utf-8")
        console.print(f"[green]Exported to {output}[/green]")
    else:
        console.print(content)


@playlist_app.command("repair")
def playlist_repair(
    identifier: str = typer.Argument(..., help="Playlist ID or Name to analyze"),
    threshold: float = typer.Option(
        0.8, "--threshold", "-t", help="Minimum similarity score (0.0 to 1.0) to replace [default: 0.8]."
    ),
    dry_run: bool = typer.Option(
        True, "--dry-run/--live", help="Simulate replacements without modifying playlist on Tidal."
    ),
    export: Optional[Path] = typer.Option(
        None, "--export", "-e", help="Custom path for diagnostic JSON report [default: reports/repair_<playlist>_<date>.json]"
    ),
    all_tracks: bool = typer.Option(
        False, "--all-tracks", help="Inspect all tracks in playlist, not just unavailable ones."
    ),
) -> None:
    """Detect unavailable tracks in playlist and evaluate replacement candidates."""
    mgr = SessionManager(state["config"])
    try:
        session = mgr.load_session()
        from tidal_cli.services.playlist_repair_service import PlaylistRepairService
        repair_svc = PlaylistRepairService(session)
        report = repair_svc.analyze_and_repair(
            identifier=identifier,
            threshold=threshold,
            dry_run=dry_run,
            all_tracks=all_tracks,
            export_path=export,
        )
    except Exception as e:
        err_console.print(f"[bold red]Repair analysis failed:[/bold red] {e}")
        raise typer.Exit(code=1)

    if state["json_mode"]:
        console.print_json(data=report)
        return

    unavail_count = report["unavailable_tracks_count"]
    mode_str = "[yellow]Dry-Run (Simulation)[/yellow]" if dry_run else "[red]LIVE (Mutating)[/red]"
    title = f"Playlist Repair: {report['playlist_name']} ({mode_str}, Threshold: {threshold*100:.0f}%)"

    if unavail_count == 0:
        console.print(
            Panel(
                f"[green]All {report['total_tracks']} tracks are active and playable![/green]",
                title=title,
                border_style="green",
            )
        )
        console.print(f"[dim]Diagnostic report written to: {report['exported_to']}[/dim]")
        return

    table = Table(title=title, border_style="yellow")
    table.add_column("#", justify="right", style="dim")
    table.add_column("Unavailable Track", style="bold red")
    table.add_column("Artist")
    table.add_column("Best Candidate", style="bold green")
    table.add_column("Score", justify="right")
    table.add_column("Action / Status")

    for u in report["unavailable_tracks"]:
        orig = u["original_track"]
        best = u.get("best_candidate")
        idx_str = str(u["index_in_playlist"] + 1)
        orig_title = orig["title"]
        orig_artist = orig["artist"]

        if best:
            cand_title = f"{best['title']} (ID: {best['id']})"
            score_pct = f"{best['similarity_score']*100:.1f}%"
            score_style = (
                "bold green"
                if best["similarity_score"] >= 0.8
                else ("bold yellow" if best["similarity_score"] >= 0.5 else "dim red")
            )
            status = u["replacement_status"]
            if best["requires_manual_approval"]:
                status = f"[yellow]{status}[/yellow] (needs manual approval)"
            table.add_row(
                idx_str,
                orig_title,
                orig_artist,
                cand_title,
                f"[{score_style}]{score_pct}[/{score_style}]",
                status,
            )
        else:
            table.add_row(
                idx_str,
                orig_title,
                orig_artist,
                "[dim]No candidate found[/dim]",
                "0.0%",
                "[red]no_candidates[/red]",
            )

    console.print(table)
    console.print(f"\n[bold]Full diagnostic report written to:[/bold] [cyan]{report['exported_to']}[/cyan]")
    if dry_run and threshold >= 1.0:
        console.print(
            "[dim]Note: Baseline threshold is set to 100% and Dry-Run is active. No tracks were replaced.[/dim]"
        )


@playlist_app.command("snapshot")
def playlist_snapshot(
    identifier: Optional[str] = typer.Argument(
        None, help="Playlist ID or Name (leave empty or specify 'all' to snapshot all personal playlists)"
    ),
    alert: bool = typer.Option(True, "--alert/--no-alert", help="Record alerts if unavailable tracks are detected"),
    notify: bool = typer.Option(True, "--notify/--no-notify", help="Dispatch email notification if configured and changes/alerts detected"),
    all_mail: bool = typer.Option(False, "--all-mail", "-a", help="Send full 'All Playlists Baseline Overview' email"),
) -> None:
    """Create a versioned snapshot of personal playlists and record unavailable track alerts."""
    mgr = SessionManager(state["config"])
    notif_info = None
    try:
        session = mgr.load_session()
        snapshot_svc = SnapshotService(session, cfg=state["config"])
        if identifier and identifier.lower() != "all":
            result = snapshot_svc.snapshot_playlist(identifier, alert_on_unavailable=alert)
            snapshots = [result]
        else:
            batch_res = snapshot_svc.snapshot_all_playlists(
                alert_on_unavailable=alert,
                notify=notify,
                force_all_mail=all_mail,
            )
            snapshots = batch_res.get("playlists", [])
            notif_info = batch_res.get("notification")
    except Exception as e:
        err_console.print(f"[bold red]Snapshot error:[/bold red] {e}")
        raise typer.Exit(code=1)

    if state["json_mode"]:
        payload = {"playlists": snapshots, "notification": notif_info} if notif_info else snapshots
        console.print_json(data=payload)
        return

    table = Table(title="Playlist Snapshots & Version Tracking", border_style="cyan")
    table.add_column("Playlist", style="bold")
    table.add_column("Status")
    table.add_column("Version", justify="right")
    table.add_column("Tracks", justify="right")
    table.add_column("Unavailable", justify="right")
    table.add_column("Snapshot File", style="dim")

    for s in snapshots:
        if "error" in s:
            table.add_row(
                s.get("playlist_name", "Unknown"),
                "[red]Error[/red]",
                "-",
                "-",
                "-",
                s.get("error", ""),
            )
            continue

        status_str = "[green]Created[/green]" if s.get("status") == "created" else "[dim]Unchanged[/dim]"
        unavail = s.get("unavailable_tracks_count", 0)
        unavail_str = f"[bold red]{unavail}[/bold red]" if unavail > 0 else "[green]0[/green]"
        snap_file = Path(s["snapshot_file"]).name if s.get("snapshot_file") else "-"
        table.add_row(
            s.get("name", s.get("playlist_name", "Unknown")),
            status_str,
            str(s.get("version", "-")),
            str(s.get("track_count", s.get("total_tracks", "-"))),
            unavail_str,
            snap_file,
        )

    console.print(table)
    total_unavail = sum(s.get("unavailable_tracks_count", 0) for s in snapshots if "error" not in s)
    if total_unavail > 0:
        console.print(
            f"\n[bold yellow]Alert:[/bold yellow] Found {total_unavail} unavailable track(s) across playlists."
            f"\nRun [cyan]tidal-cli playlist alerts[/cyan] to view details or [cyan]tidal-cli playlist repair <id>[/cyan] to find replacements."
        )

    if notif_info and notif_info.get("sent"):
        tmpl = notif_info.get("template", "notification")
        console.print(f"\n[green]✓ Email notification successfully sent:[/green] {tmpl}")


@playlist_app.command("alerts")
def playlist_alerts(
    limit: int = typer.Option(50, "--limit", "-n", help="Maximum number of recent alerts to display"),
) -> None:
    """Display recent track availability alerts logged during snapshots."""
    snapshot_svc = SnapshotService(None, cfg=state["config"])
    alerts = snapshot_svc.get_recent_alerts(limit=limit)

    if state["json_mode"]:
        console.print_json(data=alerts)
        return

    if not alerts:
        console.print(
            Panel(
                "[green]No availability alerts found in log.[/green]",
                title="Track Availability Alerts",
                border_style="green",
            )
        )
        return

    table = Table(title=f"Recent Availability Alerts ({len(alerts)} entries)", border_style="red")
    table.add_column("Timestamp", style="dim")
    table.add_column("Playlist", style="bold")
    table.add_column("Track ID", justify="right")
    table.add_column("Track Name")
    table.add_column("Artist")
    table.add_column("Reason", style="red")

    for a in alerts:
        table.add_row(
            a.get("timestamp", "-"),
            a.get("playlist_name", "-"),
            str(a.get("track_id", "-")),
            a.get("track_title", "-"),
            a.get("artist_name", "-"),
            a.get("reason", "unavailable"),
        )

    console.print(table)


# ---------------------------------------------------------------------------
# Album Commands
# ---------------------------------------------------------------------------
@album_app.command("show")
def album_show(
    identifier: str = typer.Argument(..., help="Album ID or Album Title"),
) -> None:
    """Display album details and track list."""
    mgr = SessionManager(state["config"])
    try:
        session = mgr.load_session()
        svc = AlbumService(session)
        alb = svc.get_album(identifier)
        if not alb:
            err_console.print(f"[bold red]Album not found:[/bold red] {identifier}")
            raise typer.Exit(code=1)
        tracks = alb.tracks()
    except Exception as e:
        err_console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(code=1)

    if state["json_mode"]:
        from tidal_cli.services.metadata_serializer import serialize_album
        console.print_json(data=serialize_album(alb, tracks))
        return

    artist_name = getattr(getattr(alb, "artist", None), "name", "Unknown Artist")
    release_date = getattr(alb, "release_date", "") or ""
    table = Table(
        title=f"Album: {alb.name} by {artist_name} ({release_date}) - {len(tracks)} tracks",
        border_style="magenta",
    )
    table.add_column("#", justify="right", style="dim")
    table.add_column("Title", style="bold")
    table.add_column("Duration", justify="right")
    table.add_column("ISRC", style="dim")

    for idx, t in enumerate(tracks, 1):
        dur_secs = getattr(t, "duration", 0)
        dur_str = f"{dur_secs // 60}:{dur_secs % 60:02d}"
        table.add_row(
            str(getattr(t, "track_num", idx)),
            t.name,
            dur_str,
            getattr(t, "isrc", "") or "",
        )

    console.print(table)


@album_app.command("export")
def album_export(
    identifier: str = typer.Argument(..., help="Album ID or Album Title"),
    format: str = typer.Option("json", "--format", "-f", help="Format: json, m3u8, or csv"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="File to write export to"),
) -> None:
    """Export album with all container and track metadata to JSON (or m3u8/csv)."""
    mgr = SessionManager(state["config"])
    try:
        session = mgr.load_session()
        svc = AlbumService(session)
        content = svc.export_album(identifier, export_format=format)
    except Exception as e:
        err_console.print(f"[bold red]Export failed:[/bold red] {e}")
        raise typer.Exit(code=1)

    if output:
        output.write_text(content, encoding="utf-8")
        console.print(f"[green]Exported album to {output}[/green]")
    else:
        console.print(content)


# ---------------------------------------------------------------------------
# Mix Commands
# ---------------------------------------------------------------------------
@mix_app.command("list")
def mix_list() -> None:
    """List all dynamically discovered Daily Mixes and Discovery Mix."""
    mgr = SessionManager(state["config"])
    try:
        session = mgr.load_session()
        svc = MixService(session)
        discovery, daily_mixes = svc.get_categorized_daily_mixes()
    except Exception as e:
        err_console.print(f"[bold red]Error querying mixes:[/bold red] {e}")
        raise typer.Exit(code=1)

    if state["json_mode"]:
        data = {
            "discovery_mix": {"id": discovery.id, "title": discovery.title} if discovery else None,
            "daily_mixes": [{"id": m.id, "title": m.title} for m in daily_mixes],
            "daily_mix_count": len(daily_mixes),
        }
        console.print_json(data=data)
        return

    table = Table(title=f"Discovered Mixes ({len(daily_mixes)} Daily Mixes)", border_style="magenta")
    table.add_column("Type", style="bold")
    table.add_column("Title")
    table.add_column("ID", style="dim")

    if discovery:
        table.add_row("Discovery", discovery.title, discovery.id)
    for m in daily_mixes:
        table.add_row("Daily Mix", m.title, m.id)

    console.print(table)


@mix_app.command("daily-backup")
def mix_backup(
    name_format: str = typer.Option(
        "Discovery Backup - {date}", "--format", help="Name template with {date}"
    ),
) -> None:
    """Back up today's Daily Discovery mix into a permanent dated playlist."""
    mgr = SessionManager(state["config"])
    try:
        session = mgr.load_session()
        svc = MixService(session)
        res = svc.backup_daily_discovery(playlist_name_format=name_format)
    except Exception as e:
        err_console.print(f"[bold red]Backup failed:[/bold red] {e}")
        raise typer.Exit(code=1)

    if state["json_mode"]:
        console.print_json(data=res)
    elif not state["quiet"]:
        console.print(
            f"[green]Backed up Daily Discovery ({res['tracks_added']} tracks) into '[bold]{res['playlist_name']}[/bold]'![/green]"
        )


@mix_app.command("megamix")
def mix_megamix(
    target: str = typer.Option("Daily Megamix", "--target", "-t", help="Target playlist name"),
    shuffle: bool = typer.Option(True, "--shuffle/--no-shuffle", help="Shuffle track order randomly"),
    mode: str = typer.Option("replace", "--mode", "-m", help="'replace' previous tracks or 'append'"),
    limit: Optional[int] = typer.Option(None, "--limit", "-l", help="Max tracks to add"),
) -> None:
    """Aggregate all daily mixes + discovery into a randomized Megamix."""
    mgr = SessionManager(state["config"])
    try:
        session = mgr.load_session()
        svc = MixService(session)
        res = svc.create_megamix(
            target_playlist=target,
            shuffle=shuffle,
            mode=mode,
            limit=limit,
        )
    except Exception as e:
        err_console.print(f"[bold red]Megamix generation failed:[/bold red] {e}")
        raise typer.Exit(code=1)

    if state["json_mode"]:
        console.print_json(data=res)
    elif not state["quiet"]:
        console.print(
            f"[bold green]Megamix Created/Updated![/bold green]\n"
            f"Playlist: [bold]{res['target_playlist']}[/bold] (ID: {res['playlist_id']})\n"
            f"Processed mixes: {res['mixes_processed']}\n"
            f"Unique tracks aggregated: {res['total_unique_tracks']}\n"
            f"Tracks added: {res['tracks_added']} (Mode: {res['mode']}, Shuffled: {res['shuffled']})"
        )


# ---------------------------------------------------------------------------
# Job Commands (for cron)
# ---------------------------------------------------------------------------
@job_app.command("list")
def job_list() -> None:
    """List all automated jobs defined in jobs.yaml."""
    runner = JobRunner(state["config"])
    try:
        jobs = runner.list_jobs()
    except Exception as e:
        err_console.print(f"[bold red]Error loading jobs:[/bold red] {e}")
        raise typer.Exit(code=1)

    if state["json_mode"]:
        console.print_json(data=jobs)
        return

    table = Table(title="Configured Automation Jobs (jobs.yaml)", border_style="yellow")
    table.add_column("Job Name", style="bold")
    table.add_column("Description")

    for name, desc in jobs.items():
        table.add_row(name, desc)

    console.print(table)


@job_app.command("run")
def job_run(
    job_name: str = typer.Argument(..., help="Name of job defined in jobs.yaml"),
) -> None:
    """Execute a configured job headlessly (designed for cron)."""
    runner = JobRunner(state["config"])
    try:
        res = runner.run_job(job_name)
    except Exception as e:
        err_console.print(f"[bold red]Job '{job_name}' failed:[/bold red] {e}")
        raise typer.Exit(code=1)

    if state["json_mode"]:
        console.print_json(data=res)
    elif not state["quiet"]:
        console.print(f"[green]Job '[bold]{job_name}[/bold]' finished successfully![/green]")


@job_app.command("cron-help")
def job_cron_help(
    job_name: str = typer.Option("daily-discovery-megamix", "--job", "-j", help="Job name")
) -> None:
    """Generate ready-to-copy crontab lines for Linux servers."""
    runner = JobRunner(state["config"])
    line = runner.generate_cron_help(job_name=job_name)
    console.print(Panel(line, title="[bold]Recommended Crontab Configuration[/bold]", border_style="cyan"))


# ---------------------------------------------------------------------------
# Notification Commands
# ---------------------------------------------------------------------------
@notify_app.command("status")
def notify_status() -> None:
    """Display current SMTP email notification configuration and status."""
    email_cfg = state["config"].get_email_config()

    state_data = {}
    if state["config"].notification_state_file.exists():
        try:
            with open(state["config"].notification_state_file, "r", encoding="utf-8") as f:
                state_data = json.load(f) or {}
        except Exception:
            state_data = {}

    if state["json_mode"]:
        data = {
            "configured": email_cfg.is_configured,
            "config": email_cfg.model_dump(exclude={"smtp_password"}),
            "password_set": bool(email_cfg.smtp_password),
            "state": state_data,
        }
        console.print_json(data=data)
        return

    table = Table(title="Email Notification Status", border_style="cyan")
    table.add_column("Setting", style="bold")
    table.add_column("Value")

    table.add_row(
        "Configured",
        "[green]Yes[/green]" if email_cfg.is_configured else "[yellow]No (smtp_host or to_address missing)[/yellow]",
    )
    table.add_row("SMTP Host", email_cfg.smtp_host or "[dim]Not set[/dim]")
    table.add_row("SMTP Port", str(email_cfg.smtp_port))
    table.add_row("TLS / STARTTLS", "Yes" if email_cfg.use_tls else "No")
    table.add_row("SSL", "Yes" if email_cfg.use_ssl else "No")
    table.add_row("SMTP User", email_cfg.smtp_user or "[dim]Not set[/dim]")
    table.add_row("Password Set", "[green]Yes[/green]" if email_cfg.smtp_password else "[dim]No[/dim]")
    table.add_row("From Address", email_cfg.from_address or email_cfg.smtp_user or "[dim]Default[/dim]")
    table.add_row("To Recipient(s)", email_cfg.to_address or "[dim]Not set[/dim]")
    table.add_row("First Email Sent", state_data.get("first_email_sent_at", "[dim]Never[/dim]"))
    table.add_row("Last Email Sent", state_data.get("last_email_sent_at", "[dim]Never[/dim]"))
    if state_data.get("last_email_subject"):
        table.add_row("Last Subject", state_data.get("last_email_subject", ""))

    console.print(table)


@notify_app.command("test")
def notify_test() -> None:
    """Send a test email to verify SMTP configuration and network connectivity."""
    from tidal_cli.services.email_service import EmailService

    email_svc = EmailService(cfg=state["config"])
    if not email_svc.email_config.is_configured:
        err_console.print(
            "[bold red]Email not configured.[/bold red] Please run [cyan]tidal-cli notify config[/cyan] or set TIDAL_SMTP_* environment variables."
        )
        raise typer.Exit(code=1)

    console.print(
        f"Sending test email to [cyan]{email_svc.email_config.to_address}[/cyan] via [bold]{email_svc.email_config.smtp_host}[/bold]..."
    )
    res = email_svc.send_test_email()

    if state["json_mode"]:
        console.print_json(data=res)
        return

    if res.get("success"):
        console.print("[green]✓ Test email delivered successfully![/green]")
    else:
        err_console.print(f"[bold red]Delivery failed:[/bold red] {res.get('error')}")
        raise typer.Exit(code=1)


@notify_app.command("send")
def notify_send(
    all_playlists: bool = typer.Option(
        False, "--all", "-a", help="Send the complete 'All Playlists Baseline Overview' email instead of changes digest"
    ),
) -> None:
    """Manually dispatch an email digest (or all playlists overview with --all)."""
    mgr = SessionManager(state["config"])
    try:
        session = mgr.load_session()
        snapshot_svc = SnapshotService(session, cfg=state["config"])
        batch_res = snapshot_svc.snapshot_all_playlists(
            alert_on_unavailable=True, notify=True, force_all_mail=all_playlists
        )
    except Exception as e:
        err_console.print(f"[bold red]Failed to dispatch notification:[/bold red] {e}")
        raise typer.Exit(code=1)

    notif = batch_res.get("notification") or {}
    if state["json_mode"]:
        console.print_json(data=notif)
        return

    if notif.get("sent"):
        tmpl = notif.get("template", "email")
        console.print(f"[green]✓ Email notification successfully sent![/green] (Template: [bold]{tmpl}[/bold])")
    else:
        reason = notif.get("reason", notif.get("error", "Unknown"))
        if reason == "not_configured":
            console.print(
                "[yellow]Email notifications are not configured in config.json or environment variables.[/yellow]"
            )
        elif reason == "no_changes_detected":
            console.print(
                "[cyan]No changes or alerts detected since last snapshot. Email was skipped.[/cyan] (Use [bold]--all[/bold] to send full overview)."
            )
        else:
            err_console.print(f"[red]Notification dispatch error:[/red] {reason}")


@notify_app.command("config")
def notify_configure(
    host: Optional[str] = typer.Option(None, "--host", "-h", help="SMTP server host (e.g. mail.example.com)"),
    port: Optional[int] = typer.Option(None, "--port", "-p", help="SMTP server port (e.g. 25, 587 for TLS, 465 for SSL)"),
    user: Optional[str] = typer.Option(None, "--user", "-u", help="SMTP username (optional for open relays)"),
    password: Optional[str] = typer.Option(None, "--password", help="SMTP password (optional for open relays)"),
    from_addr: Optional[str] = typer.Option(None, "--from-addr", help="Sender email address"),
    to_addr: Optional[str] = typer.Option(None, "--to-addr", help="Recipient email address"),
    tls: Optional[bool] = typer.Option(None, "--tls/--no-tls", help="Enable/disable STARTTLS (e.g. on port 25 or 587)"),
    ssl: Optional[bool] = typer.Option(None, "--ssl/--no-ssl", help="Enable/disable direct SSL (e.g. on port 465)"),
) -> None:
    """Configure or update SMTP email notification settings in config.json."""
    curr = state["config"].get_email_config()
    update_dict = {
        "smtp_host": host if host is not None else curr.smtp_host,
        "smtp_port": port if port is not None else curr.smtp_port,
        "smtp_user": user if user is not None else curr.smtp_user,
        "smtp_password": password if password is not None else curr.smtp_password,
        "from_address": from_addr if from_addr is not None else curr.from_address,
        "to_address": to_addr if to_addr is not None else curr.to_address,
        "use_tls": tls if tls is not None else curr.use_tls,
        "use_ssl": ssl if ssl is not None else curr.use_ssl,
    }
    updated = curr.model_copy(update={k: v for k, v in update_dict.items() if v is not None})
    state["config"].save_email_config(updated)
    console.print(f"[green]Saved email configuration to {state['config'].config_file}[/green]")
    if updated.is_configured:
        console.print("[green]Email notifications are now enabled.[/green] Run [cyan]tidal-cli notify test[/cyan] to verify.")
    else:
        console.print(
            "[yellow]Note: smtp_host and to_address are both required before notifications can be dispatched.[/yellow]"
        )


if __name__ == "__main__":
    app()

