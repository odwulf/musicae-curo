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

app.add_typer(auth_app)
app.add_typer(playlist_app)
app.add_typer(album_app)
app.add_typer(mix_app)
app.add_typer(job_app)

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


if __name__ == "__main__":
    app()

