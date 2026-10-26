"""CLI interface for Musicae Curo (curo) built with Typer and Rich."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from curo.config import ConfigManager, EmailConfig, config_manager
from curo.core.provider import MusicProvider
from curo.core.registry import ProviderRegistry
import curo.providers  # Ensure all provider adapters are registered
from curo.providers.base import ProviderStub
from curo.services.album_service import AlbumService
from curo.services.email_service import EmailService
from curo.services.job_runner import JobExecutionError, JobRunner
from curo.services.mix_service import MixService
from curo.services.playlist_repair_service import PlaylistRepairService
from curo.services.playlist_service import PlaylistService
from curo.services.playlist_sync_service import PlaylistSyncService
from curo.services.snapshot_service import SnapshotService

app = typer.Typer(
    name="curo",
    help="Musicae Curo -- Universal music library custodian, playlist protector, and daily discovery mix archiver.",
    add_completion=False,
)

auth_app = typer.Typer(name="auth", help="Manage streaming service authentication and sessions.")
playlist_app = typer.Typer(name="playlist", help="Inspect, create, export, snapshot, repair, and replace playlist tracks.")
album_app = typer.Typer(name="album", help="Inspect and export albums with full metadata.")
mix_app = typer.Typer(name="mix", help="Daily discovery archiving and randomized megamix routines.")
job_app = typer.Typer(name="job", help="Manage and execute automated cron jobs.")
notify_app = typer.Typer(name="notify", help="Configure and dispatch email digests and availability notifications.")
provider_app = typer.Typer(name="provider", help="Manage configured music streaming providers.")
favorites_app = typer.Typer(name="favorites", help="Inspect, snapshot, and backup favorite/liked tracks.")

app.add_typer(auth_app)
app.add_typer(playlist_app)
app.add_typer(album_app)
app.add_typer(mix_app)
app.add_typer(job_app)
app.add_typer(notify_app)
app.add_typer(provider_app)
app.add_typer(favorites_app)

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


def parse_threshold_pct(val: Any) -> float:
    """Parse percentage threshold like '80', '80%', '80.0', or legacy '0.8'."""
    s = str(val).strip().rstrip("%")
    try:
        v = float(s)
    except ValueError:
        raise typer.BadParameter(f"Threshold must be a valid number, got '{val}'")
    if v < 0 or v > 100:
        raise typer.BadParameter(f"Threshold must be between 0 and 100%, got {v}")
    if 0.0 < v <= 1.0:
        return v * 100.0
    return v


# ---------------------------------------------------------------------------
# Global State
# ---------------------------------------------------------------------------
state: Dict[str, Any] = {
    "config": config_manager,
    "provider_name": None,
    "quiet": False,
    "json_mode": False,
}


def get_active_provider(
    provider_override: Optional[str] = None, require_active: bool = False
) -> MusicProvider:
    """Resolve and return active MusicProvider adapter."""
    p_name = provider_override or state.get("provider_name") or state["config"].get_default_provider()
    p = ProviderRegistry.create_provider(p_name, cfg=state["config"])
    if require_active and isinstance(p, ProviderStub):
        err_console.print(
            f"[bold yellow]Provider '{p.display_name}' is scaffolded and not yet implemented.[/bold yellow]\n"
            f"[dim]Run 'curo provider list' to view available providers.[/dim]"
        )
        raise typer.Exit(1)
    return p


@app.callback()
def main(
    config: Optional[Path] = typer.Option(
        None, "--config", "-c", help="Path to config directory [default: ~/.config/curo]"
    ),
    provider: Optional[str] = typer.Option(
        None, "--provider", "-p", help="Streaming provider (e.g. tidal, deezer, spotify) [default from config]"
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
    if provider:
        state["provider_name"] = provider.lower()
    state["quiet"] = quiet
    state["json_mode"] = json_mode
    setup_logging(verbose=verbose, log_file=state["config"].log_file)


# ---------------------------------------------------------------------------
# Provider Commands
# ---------------------------------------------------------------------------
@provider_app.command("list")
def provider_list() -> None:
    """List all available and registered streaming provider adapters."""
    providers = ProviderRegistry.list_providers()
    default_p = state["config"].get_default_provider()

    if state["json_mode"]:
        console.print(json.dumps({"default": default_p, "providers": providers}, indent=2))
        return

    table = Table(title="Available Music Providers", border_style="cyan")
    table.add_column("Provider", style="bold")
    table.add_column("Default", justify="center")
    table.add_column("Status")

    for p in providers:
        is_def = "* (default)" if p == default_p else ""
        status = "Active" if p in ("tidal", "spotify", "deezer") else "Scaffolded (stub)"
        table.add_row(p, is_def, status)

    console.print(table)


@provider_app.command("default")
def provider_default(
    name: Optional[str] = typer.Argument(None, help="Provider name to set as default (e.g. tidal, deezer)")
) -> None:
    """View or set default active provider."""
    if name:
        key = name.strip().lower()
        if key not in ProviderRegistry.list_providers():
            err_console.print(f"[bold red]Unknown provider '{name}'. Available: {', '.join(ProviderRegistry.list_providers())}[/bold red]")
            raise typer.Exit(1)
        state["config"].set_default_provider(key)
        console.print(f"[bold green]Default provider set to '{key}'.[/bold green]")
    else:
        current = state["config"].get_default_provider()
        if state["json_mode"]:
            console.print_json(data={"default_provider": current})
        else:
            console.print(f"Current default provider: [bold cyan]{current}[/bold cyan]")


# ---------------------------------------------------------------------------
# Auth Commands
# ---------------------------------------------------------------------------
@auth_app.command("login")
def auth_login(
    provider: Optional[str] = typer.Option(None, "-p", "--provider", help="Provider to log in to")
) -> None:
    """Log in to a streaming provider."""
    p = get_active_provider(provider)
    if isinstance(p, ProviderStub):
        console.print(
            Panel(
                f"[bold yellow]{p.display_name} adapter is scaffolded, but not yet implemented.[/bold yellow]\n\n"
                f"[dim]Musicae Curo currently supports [bold]Tidal[/bold], [bold]Spotify[/bold], and [bold]Deezer[/bold] as active providers.\n"
                f"Support for {p.display_name} is planned. Contributions are welcome!\n"
                f"Run [bold]curo provider list[/bold] to check provider statuses.[/dim]",
                title=f"{p.display_name} Provider",
                border_style="yellow",
            )
        )
        raise typer.Exit(1)

    try:
        p.login()
    except Exception as e:
        err_console.print(f"[bold red]Login failed for {p.display_name}: {e}[/bold red]")
        raise typer.Exit(1)


@auth_app.command("logout")
def auth_logout(
    provider: Optional[str] = typer.Option(None, "-p", "--provider", help="Provider to log out from")
) -> None:
    """Log out and remove stored session credentials."""
    p = get_active_provider(provider)
    if isinstance(p, ProviderStub):
        console.print(f"[dim]{p.display_name} is a scaffolded stub; no stored session exists.[/dim]")
        return
    p.logout()
    console.print(f"[bold yellow]Logged out of {p.display_name}.[/bold yellow]")


@auth_app.command("status")
def auth_status(
    provider: Optional[str] = typer.Option(None, "-p", "--provider", help="Provider to inspect")
) -> None:
    """Check session authentication status."""
    p = get_active_provider(provider)
    if isinstance(p, ProviderStub):
        if state["json_mode"]:
            console.print_json(data=p.get_session_info())
            return
        console.print(
            Panel(
                f"[bold yellow]{p.display_name} is scaffolded (stub) and not yet implemented.[/bold yellow]\n\n"
                f"[dim]Tidal and Spotify are currently active providers. Run [bold]curo provider list[/bold] for status.[/dim]",
                title=f"{p.display_name} Auth Status",
                border_style="yellow",
            )
        )
        return

    info = p.get_session_info()
    if state["json_mode"]:
        console.print_json(data=info)
        return

    if info.get("logged_in"):
        msg = f"[bold green]Authenticated on {p.display_name} as {info.get('user_name')} (ID: {info.get('user_id')})[/bold green]"
    else:
        msg = f"[bold red]Not authenticated on {p.display_name}. Run 'curo auth login -p {p.name}' to connect.[/bold red]"
    console.print(Panel(msg, title=f"{p.display_name} Auth Status", border_style="cyan"))


# ---------------------------------------------------------------------------
# Playlist Commands
# ---------------------------------------------------------------------------
@playlist_app.command("list")
def playlist_list(
    provider: Optional[str] = typer.Option(None, "-p", "--provider", help="Target streaming provider")
) -> None:
    """List all user playlists."""
    p = get_active_provider(provider, require_active=True)
    svc = PlaylistService(p)
    playlists = svc.list_playlists()

    if state["json_mode"]:
        console.print(json.dumps(playlists, indent=2, default=str))
        return

    table = Table(title=f"{p.display_name} User Playlists", border_style="cyan")
    table.add_column("Title", style="bold")
    table.add_column("Tracks", justify="right")
    table.add_column("ID", style="dim")

    for pl in playlists:
        table.add_row(pl.get("title", ""), str(pl.get("num_tracks", 0)), str(pl.get("id", "")))

    console.print(table)


@playlist_app.command("show")
def playlist_show(
    identifier: str = typer.Argument(..., help="Playlist title or ID"),
    provider: Optional[str] = typer.Option(None, "-p", "--provider", help="Target streaming provider"),
) -> None:
    """Inspect tracks in a playlist."""
    p = get_active_provider(provider, require_active=True)
    svc = PlaylistService(p)
    try:
        pl = svc.get_playlist(identifier)
        if not pl:
            err_console.print(f"[bold red]Playlist '{identifier}' not found.[/bold red]")
            raise typer.Exit(1)
        tracks = svc.get_tracks(identifier)
    except Exception as e:
        err_console.print(f"[bold red]Error: {e}[/bold red]")
        raise typer.Exit(1)

    if state["json_mode"]:
        from curo.services.metadata_serializer import serialize_playlist
        console.print(json.dumps(serialize_playlist(pl, tracks), indent=2, default=str))
        return

    pl_title = getattr(pl, "title", None) or getattr(pl, "name", "Playlist")
    table = Table(title=f"Playlist: {pl_title} ({len(tracks)} tracks)", border_style="cyan")
    table.add_column("#", justify="right")
    table.add_column("Title", style="bold")
    table.add_column("Artist")
    table.add_column("Duration", justify="right")
    table.add_column("ISRC", style="dim")

    for idx, t in enumerate(tracks, 1):
        dur = getattr(t, "duration_seconds", None) or getattr(t, "duration", 0)
        dur_str = f"{dur//60}:{dur%60:02d}"
        art = getattr(getattr(t, "artist", None), "name", "Unknown Artist")
        table.add_row(
            str(idx),
            getattr(t, "title", None) or getattr(t, "name", ""),
            art,
            dur_str,
            str(getattr(t, "isrc", "") or ""),
        )

    console.print(table)


@playlist_app.command("create")
def playlist_create(
    title: str = typer.Argument(..., help="Playlist title"),
    desc: str = typer.Option("", "--desc", help="Description"),
    public: bool = typer.Option(False, "--public", help="Make playlist public"),
    folder: Optional[str] = typer.Option(None, "--folder", "-f", help="Target playlist folder name or ID"),
    provider: Optional[str] = typer.Option(None, "-p", "--provider", help="Target streaming provider"),
) -> None:
    """Create a new playlist."""
    p = get_active_provider(provider, require_active=True)
    svc = PlaylistService(p)
    res = svc.create_playlist(title=title, description=desc, public=public, folder=folder)
    pl_id = getattr(res, "id", "")
    if state["json_mode"]:
        data: Dict[str, Any] = {"id": str(pl_id), "title": title, "provider": p.name}
        if folder:
            data["folder"] = folder
        console.print_json(data=data)
    else:
        folder_msg = f" in folder '{folder}'" if folder else ""
        console.print(f"[bold green]Created playlist '{title}' (ID: {pl_id}){folder_msg} on {p.display_name}.[/bold green]")


@playlist_app.command("folders")
def playlist_folders(
    provider: Optional[str] = typer.Option(None, "-p", "--provider", help="Target streaming provider"),
) -> None:
    """List playlist folders in your library."""
    p = get_active_provider(provider, require_active=True)
    svc = PlaylistService(p)
    folders = svc.get_folders()
    if state["json_mode"]:
        console.print_json(data=folders)
        return
    if not folders:
        console.print(f"[dim]No playlist folders found on {p.display_name}.[/dim]")
        return
    table = Table(title=f"Playlist Folders on {p.display_name}", show_header=True, header_style="bold magenta")
    table.add_column("Folder Name", style="cyan")
    table.add_column("Items", justify="right")
    table.add_column("Folder ID", style="dim")
    for f in folders:
        table.add_row(f.get("name", "Untitled"), str(f.get("total_items", 0)), str(f.get("id", "")))
    console.print(table)


@playlist_app.command("move")
def playlist_move(
    identifier: str = typer.Argument(..., help="Playlist title or ID to move"),
    folder: str = typer.Argument(..., help="Destination folder name or ID"),
    provider: Optional[str] = typer.Option(None, "-p", "--provider", help="Target streaming provider"),
) -> None:
    """Move an existing playlist into a folder."""
    p = get_active_provider(provider, require_active=True)
    svc = PlaylistService(p)
    pl = svc.get_playlist(identifier)
    if not pl:
        err_console.print(f"[bold red]Playlist not found: {identifier}[/bold red]")
        raise typer.Exit(1)
    pl_id = getattr(pl, "id", identifier)
    success = svc.move_playlist_to_folder(str(pl_id), folder)
    if not success:
        err_console.print(f"[bold red]Failed to move playlist to folder '{folder}' on {p.display_name}.[/bold red]")
        raise typer.Exit(1)
    pl_title = getattr(pl, "title", identifier)
    if state["json_mode"]:
        console.print_json(data={"success": True, "playlist_id": str(pl_id), "title": pl_title, "folder": folder})
    else:
        console.print(f"[bold green]Moved playlist '{pl_title}' into folder '{folder}' on {p.display_name}.[/bold green]")


@playlist_app.command("export")
def playlist_export(
    identifier: str = typer.Argument(..., help="Playlist title or ID"),
    format: str = typer.Option("json", "--format", "-f", help="Export format: json, m3u8, csv"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="File path to save export"),
    provider: Optional[str] = typer.Option(None, "-p", "--provider", help="Target streaming provider"),
) -> None:
    """Export playlist metadata to JSON, M3U8, or CSV."""
    p = get_active_provider(provider, require_active=True)
    svc = PlaylistService(p)
    try:
        content = svc.export_playlist(identifier, export_format=format)
        if output:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(content, encoding="utf-8")
            console.print(f"[bold green]Exported playlist to {output}[/bold green]")
        else:
            console.print(content)
    except Exception as e:
        err_console.print(f"[bold red]Export failed: {e}[/bold red]")
        raise typer.Exit(1)


@playlist_app.command("snapshot")
def playlist_snapshot(
    identifier: Optional[str] = typer.Argument(
        None, help="Playlist ID or Name (leave empty or specify 'all' to snapshot all personal playlists)"
    ),
    alert: bool = typer.Option(True, "--alert/--no-alert", help="Record alerts if unavailable tracks are detected"),
    notify: bool = typer.Option(True, "--notify/--no-notify", help="Dispatch email notification if configured and changes/alerts detected"),
    all_mail: bool = typer.Option(False, "--all-mail", "-a", help="Send full 'All Playlists Baseline Overview' email"),
    provider: Optional[str] = typer.Option(None, "-p", "--provider", help="Target streaming provider"),
    quiet: bool = typer.Option(False, "-q", "--quiet", help="Suppress console output (ideal for cron)."),
    json_mode: bool = typer.Option(False, "--json", help="Output machine-readable JSON."),
) -> None:
    """Save versioned SHA-256 snapshot of personal playlists."""
    if quiet:
        state["quiet"] = True
    if json_mode:
        state["json_mode"] = True

    p = get_active_provider(provider, require_active=True)
    snap_svc = SnapshotService(p, cfg=state["config"])
    notif_info = None

    try:
        if identifier and identifier.lower() != "all":
            result = snap_svc.snapshot_playlist(identifier, alert_on_unavailable=alert)
            snapshots = [result]
        else:
            batch = snap_svc.snapshot_all_playlists(
                alert_on_unavailable=alert,
                notify=notify,
                force_all_mail=all_mail,
            )
            snapshots = batch.get("playlists", [])
            notif_info = batch.get("notification")
    except Exception as e:
        err_console.print(f"[bold red]Snapshot error: {e}[/bold red]")
        raise typer.Exit(1)

    if state["json_mode"]:
        payload = {"playlists": snapshots, "notification": notif_info} if notif_info else snapshots
        console.print_json(data=payload)
        return

    if state["quiet"]:
        return

    table = Table(title=f"Playlist Snapshots ({p.display_name})", border_style="cyan")
    table.add_column("Playlist", style="bold")
    table.add_column("Status")
    table.add_column("Version", justify="right")
    table.add_column("Tracks", justify="right")
    table.add_column("Unavailable", justify="right")
    table.add_column("Snapshot File", style="dim")

    for s in snapshots:
        if "error" in s:
            table.add_row(s.get("playlist_name", "Unknown"), "[red]Error[/red]", "-", "-", "-", s.get("error", ""))
            continue
        status_str = "[green]Created[/green]" if s.get("status") == "created" else "[dim]Unchanged[/dim]"
        unavail = s.get("unavailable_tracks_count", s.get("unavailable_count", 0))
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


@playlist_app.command("alerts")
def playlist_alerts(
    limit: int = typer.Option(50, "--limit", "-l", help="Maximum alert entries to return"),
) -> None:
    """Display recent unavailable / delisted track alerts."""
    snap_svc = SnapshotService(cfg=state["config"])
    alerts = snap_svc.get_recent_alerts(limit=limit)

    if state["json_mode"]:
        console.print_json(data=alerts)
        return

    if not alerts:
        console.print("[dim]No availability alerts recorded.[/dim]")
        return

    table = Table(title="Recent Track Availability Alerts", border_style="red")
    table.add_column("Date", style="dim")
    table.add_column("Playlist", style="bold")
    table.add_column("Track")
    table.add_column("Artist")

    for a in alerts:
        table.add_row(
            str(a.get("timestamp", ""))[:19],
            str(a.get("playlist_name", "")),
            str(a.get("title", "")),
            str(a.get("artist", "")),
        )

    console.print(table)


@playlist_app.command("repair")
def playlist_repair(
    identifier: str = typer.Argument(..., help="Playlist title or ID"),
    threshold: float = typer.Option(
        80.0,
        "--threshold",
        "-t",
        parser=parse_threshold_pct,
        help="Minimum similarity threshold percentage (0 - 100, default: 80)",
    ),
    live: bool = typer.Option(False, "--live", help="Apply replacements live on streaming provider (default: dry-run)"),
    all_tracks: bool = typer.Option(False, "--all-tracks", help="Inspect all tracks, not just unavailable ones"),
    export: Optional[Path] = typer.Option(None, "--export", help="Custom JSON report export path"),
    track: Optional[str] = typer.Option(None, "--track", help="Target a specific track ID or playlist position (#index)"),
    with_replacement: Optional[str] = typer.Option(None, "--with", "--replacement", help="Replacement track ID, ISRC, or UPC"),
    isrc: Optional[str] = typer.Option(None, "--isrc", help="Explicit replacement ISRC sound recording code"),
    upc: Optional[str] = typer.Option(None, "--upc", help="Explicit replacement album UPC barcode"),
    provider: Optional[str] = typer.Option(None, "-p", "--provider", help="Target streaming provider"),
) -> None:
    """Analyze unavailable tracks in a playlist, score replacements, and optionally repair."""
    p = get_active_provider(provider, require_active=True)
    repair_svc = PlaylistRepairService(p, cfg=state["config"])
    threshold_ratio = threshold / 100.0 if threshold > 1.0 else threshold

    try:
        report = repair_svc.analyze_and_repair(
            identifier=identifier,
            threshold=threshold_ratio,
            dry_run=not live,
            all_tracks=all_tracks,
            export_path=export,
            target_track=track,
            replacement=with_replacement,
            isrc=isrc,
            upc=upc,
        )
    except Exception as e:
        err_console.print(f"[bold red]Repair failed: {e}[/bold red]")
        raise typer.Exit(1)

    if state["json_mode"]:
        console.print_json(data=report)
        return

    unavail_count = report["unavailable_tracks_count"]
    mode_str = "[bold yellow]DRY-RUN (Simulated)[/bold yellow]" if not live else "[bold green]LIVE (Mutating)[/bold green]"
    title = f"Playlist Repair: {report['playlist_name']} ({mode_str}, Threshold: {int(threshold_ratio * 100)}%)"

    if unavail_count == 0:
        console.print(
            Panel(
                f"[green]All {report['total_tracks']} tracks are active and playable on {p.display_name}![/green]",
                title=title,
                border_style="green",
            )
        )
        console.print(f"[dim]Diagnostic report written to: {report['exported_to']}[/dim]\n")
        return

    table = Table(title=title, border_style="yellow")
    table.add_column("#", justify="right", style="dim")
    table.add_column("Unavailable Track", style="bold red")
    table.add_column("Artist")
    table.add_column("Best Candidate", style="bold green")
    table.add_column("Score", justify="right")
    table.add_column("Action / Status")

    for u in report.get("unavailable_tracks", []):
        orig = u.get("original_track", {})
        best = u.get("best_candidate")
        idx_str = str(u.get("index_in_playlist", 0) + 1)
        orig_title = orig.get("title", "Unknown Track")
        orig_artist = orig.get("artist", "Unknown Artist")

        if best:
            official_badge = " [cyan][Official Replacement][/cyan]" if best.get("is_provider_replacement") else ""
            cand_title = f"{best['title']} (ID: {best['id']}){official_badge}"
            score_val = best.get("similarity_score", 0.0)
            score_pct = f"{score_val * 100:.1f}%"
            score_style = (
                "bold green"
                if score_val >= 0.8
                else ("bold yellow" if score_val >= 0.5 else "dim red")
            )
            status = u.get("replacement_status", "pending")
            if best.get("requires_manual_approval"):
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

    console.print()
    console.print(table)
    console.print(f"\n[bold]Full diagnostic report written to:[/bold] [cyan]{report['exported_to']}[/cyan]\n")


@playlist_app.command("replace")
def playlist_replace(
    identifier: str = typer.Argument(..., help="Playlist title or ID"),
    track: str = typer.Option(..., "--track", help="Target track ID or position (e.g. 1439552 or #4)"),
    with_replacement: Optional[str] = typer.Option(None, "--with", "--replacement", help="Replacement track ID, ISRC, or UPC"),
    isrc: Optional[str] = typer.Option(None, "--isrc", help="Replacement ISRC sound recording code"),
    upc: Optional[str] = typer.Option(None, "--upc", help="Replacement album UPC barcode"),
    live: bool = typer.Option(False, "--live", help="Apply replacement live on streaming provider (default: dry-run)"),
    provider: Optional[str] = typer.Option(None, "-p", "--provider", help="Target streaming provider"),
) -> None:
    """Manually replace a specific track in a playlist by track ID, position, ISRC, or UPC."""
    p = get_active_provider(provider, require_active=True)
    repair_svc = PlaylistRepairService(p, cfg=state["config"])

    try:
        res = repair_svc.replace_track(
            playlist_identifier=identifier,
            target=track,
            replacement=with_replacement,
            isrc=isrc,
            upc=upc,
            dry_run=not live,
        )
    except Exception as e:
        err_console.print(f"[bold red]Replacement error: {e}[/bold red]")
        raise typer.Exit(1)

    if state["json_mode"]:
        console.print_json(data=res)
        return

    status = res.get("status")
    orig = res["original_track"]
    repl = res["replacement_track"]

    if status == "simulated":
        console.print(f"[bold cyan]Simulation (Dry-Run): Track #{res['position']} in '{res['playlist_name']}'[/bold cyan]")
        console.print(f"Original:    {orig['title']} by {orig['artist']}")
        console.print(f"Replacement: {repl['title']} by {repl['artist']} (Score: {res['similarity_score']})")
        console.print(f"[dim]Resolution method: {res['resolution_method']}. Use --live to apply.[/dim]")
    elif status == "replaced":
        console.print(
            f"[bold green]Successfully replaced track #{res['position']} '{orig['title']}' with candidate ID {repl['id']} '{repl['title']}' on {p.display_name}![/bold green]"
        )
    else:
        err_console.print(f"[bold red]Replacement status: {status}[/bold red]")


@playlist_app.command("compare")
def playlist_compare(
    source_playlist: str = typer.Argument(..., help="Source playlist title or ID"),
    target_playlist: str = typer.Argument(..., help="Target playlist title or ID"),
    from_provider: Optional[str] = typer.Option(None, "--from", "--from-provider", help="Source streaming provider [default: active provider]"),
    to_provider: Optional[str] = typer.Option(None, "--to", "--to-provider", help="Target streaming provider [default: source provider]"),
    threshold: float = typer.Option(
        80.0,
        "--threshold",
        "-t",
        parser=parse_threshold_pct,
        help="Minimum similarity threshold percentage (0 - 100, default: 80)",
    ),
    export: Optional[Path] = typer.Option(None, "--export", help="Custom JSON report export path"),
) -> None:
    """Compare two playlists across providers or within the same provider."""
    src_p = get_active_provider(from_provider, require_active=True)
    tgt_name = to_provider or from_provider
    tgt_p = get_active_provider(tgt_name, require_active=True)

    sync_svc = PlaylistSyncService(source_provider=src_p, target_provider=tgt_p, cfg=state["config"])
    threshold_ratio = threshold / 100.0 if threshold > 1.0 else threshold

    try:
        report = sync_svc.compare_playlists(
            source_identifier=source_playlist,
            target_identifier=target_playlist,
            threshold=threshold_ratio,
            export_path=export,
        )
    except Exception as e:
        err_console.print(f"[bold red]Comparison failed: {e}[/bold red]")
        raise typer.Exit(1)

    if state["json_mode"]:
        console.print_json(data=report)
        return

    title = f"Playlist Comparison: '{report['source_playlist_name']}' [{src_p.display_name}] -> '{report['target_playlist_name']}' [{tgt_p.display_name}]"
    table = Table(title=title, border_style="cyan")
    table.add_column("#", justify="right", style="dim")
    table.add_column(f"Source Track ({src_p.display_name})", style="bold")
    table.add_column("Artist")
    table.add_column(f"Target Resolution ({tgt_p.display_name})", style="bold green")
    table.add_column("Score", justify="right")
    table.add_column("Sync Action")

    for item in report.get("diff_items", []):
        s_idx = str(item.get("source_index") or "-")
        s_t = item.get("source_track") or {}
        t_t = item.get("target_track") or {}

        s_title = s_t.get("title") or "[dim](Not in Source)[/dim]"
        artist = s_t.get("artist") or t_t.get("artist") or ""

        if t_t and t_t.get("title"):
            cand_id = t_t.get("id")
            cand_str = f"{t_t['title']} (ID: {cand_id})" if cand_id else t_t["title"]
        else:
            cand_str = "[dim]Not found on target[/dim]"

        score = item.get("similarity_score", 0.0)
        score_pct = f"{score * 100:.0f}%" if score > 0 else "-"

        action = item.get("action", "")
        if action == "keep":
            act_str = "[green]in_sync[/green]"
        elif action == "to_add":
            act_str = "[bold yellow]to_add[/bold yellow]"
        elif action == "extra_in_target":
            act_str = "[dim cyan]extra_in_target[/dim cyan]"
        elif action == "unresolvable":
            act_str = "[bold red]unresolvable[/bold red]"
        else:
            act_str = action

        table.add_row(s_idx, s_title, artist, cand_str, score_pct, act_str)

    console.print()
    console.print(table)
    console.print(
        f"\n[bold]In Sync:[/bold] [green]{report['in_sync_count']}[/green] | "
        f"[bold]To Add:[/bold] [yellow]{report['missing_in_target_count']}[/yellow] | "
        f"[bold]Extra in Target:[/bold] [cyan]{report['extra_in_target_count']}[/cyan] | "
        f"[bold]Unresolvable:[/bold] [red]{report['unresolvable_count']}[/red]"
    )
    console.print(f"Report exported to: [dim]{report['exported_to']}[/dim]\n")


@playlist_app.command("sync")
def playlist_sync(
    source_playlist: str = typer.Argument(..., help="Source playlist title or ID"),
    target_playlist: Optional[str] = typer.Option(None, "--target", "--target-playlist", help="Target playlist title [default: same as source]"),
    from_provider: Optional[str] = typer.Option(None, "--from", "--from-provider", help="Source streaming provider [default: active provider]"),
    to_provider: Optional[str] = typer.Option(None, "--to", "--to-provider", help="Target streaming provider [default: source provider]"),
    mode: str = typer.Option("mirror", "--mode", "-m", help="Sync mode: 'mirror' (exact sequence) or 'append' (add missing only)"),
    threshold: float = typer.Option(
        80.0,
        "--threshold",
        "-t",
        parser=parse_threshold_pct,
        help="Minimum similarity threshold percentage (0 - 100, default: 80)",
    ),
    live: bool = typer.Option(False, "--live", help="Apply mutations live on target provider (default: dry-run)"),
    no_create: bool = typer.Option(False, "--no-create", help="Fail if target playlist does not exist instead of auto-creating"),
    export: Optional[Path] = typer.Option(None, "--export", help="Custom JSON report export path"),
) -> None:
    """Synchronize playlist tracks from one provider to another (or within the same provider)."""
    src_p = get_active_provider(from_provider, require_active=True)
    tgt_name = to_provider or from_provider
    tgt_p = get_active_provider(tgt_name, require_active=True)

    sync_svc = PlaylistSyncService(source_provider=src_p, target_provider=tgt_p, cfg=state["config"])
    threshold_ratio = threshold / 100.0 if threshold > 1.0 else threshold

    try:
        report = sync_svc.sync_playlists(
            source_identifier=source_playlist,
            target_identifier=target_playlist,
            mode=mode,
            threshold=threshold_ratio,
            dry_run=not live,
            create_if_missing=not no_create,
            export_path=export,
        )
    except Exception as e:
        err_console.print(f"[bold red]Sync failed: {e}[/bold red]")
        raise typer.Exit(1)

    if state["json_mode"]:
        console.print_json(data=report)
        return

    mode_str = "[bold yellow]DRY-RUN (Simulated)[/bold yellow]" if not live else "[bold green]LIVE (Mutated)[/bold green]"
    title = f"Playlist Sync: '{report['source_playlist_name']}' [{src_p.display_name}] -> '{report['target_playlist_name']}' [{tgt_p.display_name}] ({mode_str})"

    table = Table(title=title, border_style="yellow" if not live else "green")
    table.add_column("Property", style="bold")
    table.add_column("Value")

    table.add_row("Sync Mode", f"[bold]{mode}[/bold]")
    table.add_row("Source Tracks", str(report["source_tracks_count"]))
    table.add_row("Target Tracks (Before)", str(report["target_tracks_before"]))
    table.add_row("Tracks Resolved", f"[green]{report['tracks_resolved_count']}[/green]")
    table.add_row("Tracks Added", f"[bold green]{report['tracks_added']}[/bold green]")
    table.add_row("Tracks Removed", f"[red]{report['tracks_removed']}[/red]")
    table.add_row("Target Tracks (After)", str(report["target_tracks_after"]))
    if report.get("target_playlist_created"):
        table.add_row("Target Created", "[green]Yes[/green]")
    if report["tracks_unresolvable_count"] > 0:
        table.add_row("Unresolvable Tracks", f"[bold red]{report['tracks_unresolvable_count']}[/bold red]")

    console.print()
    console.print(table)

    if report["unresolvable_tracks"]:
        unavail_table = Table(title="Unresolvable Source Tracks (Not Found on Target)", border_style="red")
        unavail_table.add_column("#", justify="right", style="dim")
        unavail_table.add_column("Track", style="bold")
        unavail_table.add_column("Artist")
        unavail_table.add_column("ISRC", style="dim")
        for u in report["unresolvable_tracks"]:
            unavail_table.add_row(
                str(u.get("source_index", "-")),
                u.get("source_title", "Unknown"),
                u.get("source_artist", "Unknown"),
                u.get("source_isrc") or "[dim]None[/dim]",
            )
        console.print()
        console.print(unavail_table)

    if not live:
        console.print(
            f"\n[dim]Note: This was a simulation. To apply changes live on {tgt_p.display_name}, pass [bold]--live[/bold].[/dim]"
        )
    else:
        console.print(
            f"\n[bold green]Successfully synchronized '{report['target_playlist_name']}' on {tgt_p.display_name}![/bold green]"
        )

    console.print(f"Report exported to: [dim]{report['exported_to']}[/dim]\n")


# ---------------------------------------------------------------------------
# Album Commands
# ---------------------------------------------------------------------------
@album_app.command("show")
def album_show(
    identifier: str = typer.Argument(..., help="Album ID or title"),
    provider: Optional[str] = typer.Option(None, "-p", "--provider", help="Target streaming provider"),
) -> None:
    """Inspect album details and tracklist."""
    p = get_active_provider(provider, require_active=True)
    svc = AlbumService(p)
    try:
        alb = svc.get_album(identifier)
        if not alb:
            err_console.print(f"[bold red]Album '{identifier}' not found.[/bold red]")
            raise typer.Exit(1)
        tracks = svc.get_tracks(identifier)
    except Exception as e:
        err_console.print(f"[bold red]Error: {e}[/bold red]")
        raise typer.Exit(1)

    if state["json_mode"]:
        from curo.services.metadata_serializer import serialize_album
        console.print(json.dumps(serialize_album(alb, tracks), indent=2, default=str))
        return

    alb_title = getattr(alb, "title", None) or getattr(alb, "name", "Album")
    table = Table(title=f"Album: {alb_title} ({len(tracks)} tracks)", border_style="cyan")
    table.add_column("#", justify="right")
    table.add_column("Title", style="bold")
    table.add_column("Duration", justify="right")
    table.add_column("ISRC", style="dim")

    for idx, t in enumerate(tracks, 1):
        dur = getattr(t, "duration_seconds", None) or getattr(t, "duration", 0)
        table.add_row(
            str(idx),
            getattr(t, "title", None) or getattr(t, "name", ""),
            f"{dur//60}:{dur%60:02d}",
            str(getattr(t, "isrc", "") or ""),
        )

    console.print(table)


# ---------------------------------------------------------------------------
# Mix Commands
# ---------------------------------------------------------------------------
@mix_app.command("list")
def mix_list(
    provider: Optional[str] = typer.Option(None, "-p", "--provider", help="Target streaming provider")
) -> None:
    """List personalized discovery mixes."""
    p = get_active_provider(provider, require_active=True)
    svc = MixService(p)
    mixes = svc.discover_all_mixes()

    if state["json_mode"]:
        console.print(json.dumps([m.model_dump() if hasattr(m, "model_dump") else str(m) for m in mixes], indent=2))
        return

    table = Table(title=f"{p.display_name} Discovery Mixes", border_style="cyan")
    table.add_column("Title", style="bold")
    table.add_column("Type")
    table.add_column("Tracks", justify="right")
    table.add_column("ID", style="dim")

    for m in mixes:
        table.add_row(
            getattr(m, "title", None) or getattr(m, "name", "Mix"),
            str(getattr(m, "mix_type", "curated_mix")),
            str(getattr(m, "track_count", 0)),
            str(getattr(m, "id", "")),
        )

    console.print(table)


@mix_app.command("daily")
def mix_daily(
    format: str = typer.Option("Discovery Backup - {date}", "--format", help="Playlist name template"),
    folder: Optional[str] = typer.Option(None, "--folder", "-f", help="Target playlist folder name or ID"),
    provider: Optional[str] = typer.Option(None, "-p", "--provider", help="Target streaming provider"),
    quiet: bool = typer.Option(False, "-q", "--quiet", help="Suppress console output (ideal for cron)."),
    json_mode: bool = typer.Option(False, "--json", help="Output machine-readable JSON."),
) -> None:
    """Archive today's Daily Discovery mix into a permanent dated playlist."""
    if quiet:
        state["quiet"] = True
    if json_mode:
        state["json_mode"] = True

    p = get_active_provider(provider, require_active=True)
    svc = MixService(p)
    res = svc.backup_daily_discovery(playlist_name_format=format, folder=folder)
    if state["json_mode"]:
        console.print_json(data=res)
    elif not state["quiet"]:
        folder_msg = f" in folder '{folder}'" if folder else ""
        console.print(
            f"[bold green]Archived {res['track_count']} tracks into '{res['playlist_name']}'{folder_msg} on {p.display_name}![/bold green]"
        )


@mix_app.command("megamix")
def mix_megamix(
    target: str = typer.Option("Daily Megamix", "--target", help="Target playlist title"),
    mode: str = typer.Option("replace", "--mode", help="Mode: replace or append"),
    no_shuffle: bool = typer.Option(False, "--no-shuffle", help="Disable random shuffle"),
    exclude_recent_days: int = typer.Option(0, "--exclude-recent-days", "-e", help="Exclude tracks featured in the last N days (0 = disabled)"),
    folder: Optional[str] = typer.Option(None, "--folder", "-f", help="Target playlist folder name or ID"),
    provider: Optional[str] = typer.Option(None, "-p", "--provider", help="Target streaming provider"),
    quiet: bool = typer.Option(False, "-q", "--quiet", help="Suppress console output (ideal for cron)."),
    json_mode: bool = typer.Option(False, "--json", help="Output machine-readable JSON."),
) -> None:
    """Synthesize all daily discovery mixes into a consolidated Megamix."""
    if quiet:
        state["quiet"] = True
    if json_mode:
        state["json_mode"] = True

    p = get_active_provider(provider, require_active=True)
    svc = MixService(p)
    res = svc.create_megamix(
        target_playlist=target,
        mode=mode,
        shuffle=not no_shuffle,
        exclude_recent_days=exclude_recent_days,
        folder=folder,
    )
    if state["json_mode"]:
        console.print_json(data=res)
    elif not state["quiet"]:
        if not res.get("success"):
            console.print(f"[bold yellow]Megamix '{target}' not created: {res.get('reason')}[/bold yellow]")
        else:
            fresh_msg = f" ({res.get('excluded_recent_repeats', 0)} recent repeats excluded)" if exclude_recent_days > 0 else ""
            folder_msg = f" in folder '{folder}'" if folder else ""
            console.print(
                f"[bold green]Synthesized Megamix '{target}'{folder_msg} with {res.get('total_unique_tracks', 0)} unique tracks on {p.display_name}!{fresh_msg}[/bold green]"
            )


# ---------------------------------------------------------------------------
# Notification Commands
# ---------------------------------------------------------------------------
@notify_app.command("status")
def notify_status() -> None:
    """Check email notification configuration status."""
    cfg = state["config"]
    email_cfg = cfg.get_email_config()
    data = {
        "configured": email_cfg.is_configured,
        "config": email_cfg.model_dump(exclude_none=True),
    }
    if state["json_mode"]:
        console.print(json.dumps(data, indent=2))
        return

    if email_cfg.is_configured:
        console.print(
            f"[bold green]Email notifications configured (Host: {email_cfg.smtp_host}:{email_cfg.smtp_port}, To: {email_cfg.to_address})[/bold green]"
        )
    else:
        console.print("[dim]Email notifications not configured. Run 'curo notify config' to set up.[/dim]")


@notify_app.command("config")
def notify_config(
    host: Optional[str] = typer.Option(None, "--host", help="SMTP server host"),
    port: Optional[int] = typer.Option(None, "--port", help="SMTP server port (e.g. 587, 465, 25)"),
    user: Optional[str] = typer.Option(None, "--user", help="SMTP username"),
    password: Optional[str] = typer.Option(None, "--password", help="SMTP password"),
    from_addr: Optional[str] = typer.Option(None, "--from-addr", help="Sender email address"),
    to_addr: Optional[str] = typer.Option(None, "--to-addr", help="Recipient email address"),
    tls: bool = typer.Option(True, "--tls/--no-tls", help="Use STARTTLS encryption"),
    ssl: bool = typer.Option(False, "--ssl/--no-ssl", help="Use direct SSL encryption"),
    exclude_megamix_diff: Optional[bool] = typer.Option(None, "--exclude-megamix-diff/--include-megamix-diff", help="Exclude exhaustive track list for daily megamixes"),
) -> None:
    """Configure SMTP credentials and notification recipients."""
    cfg = state["config"]
    curr = cfg.get_email_config()

    new_cfg = EmailConfig(
        smtp_host=host if host is not None else curr.smtp_host,
        smtp_port=port if port is not None else curr.smtp_port,
        smtp_user=user if user is not None else curr.smtp_user,
        smtp_password=password if password is not None else curr.smtp_password,
        from_address=from_addr if from_addr is not None else curr.from_address,
        to_address=to_addr if to_addr is not None else curr.to_address,
        use_tls=tls,
        use_ssl=ssl,
        send_on_changes=curr.send_on_changes,
        send_on_alerts=curr.send_on_alerts,
        exclude_megamix_diff=exclude_megamix_diff if exclude_megamix_diff is not None else curr.exclude_megamix_diff,
    )
    cfg.save_email_config(new_cfg)
    console.print("[bold green]SMTP configuration updated successfully.[/bold green]")


@notify_app.command("test")
def notify_test() -> None:
    """Send a verification test email using configured SMTP settings."""
    email_svc = EmailService(cfg=state["config"])
    res = email_svc.send_test_email()
    if state["json_mode"]:
        console.print(json.dumps(res, indent=2))
        return

    if res.get("success"):
        console.print(f"[bold green]Verification email sent to {res.get('recipient')}![/bold green]")
    else:
        err_console.print(f"[bold red]Failed to send test email: {res.get('error')}[/bold red]")
        raise typer.Exit(1)


@notify_app.command("send")
def notify_send(
    all: bool = typer.Option(False, "--all", help="Send complete all-playlists baseline catalog report"),
    provider: Optional[str] = typer.Option(None, "-p", "--provider", help="Target streaming provider"),
) -> None:
    """Trigger email notification dispatch manually."""
    p = get_active_provider(provider, require_active=True)
    snap_svc = SnapshotService(p, cfg=state["config"])
    email_svc = EmailService(cfg=state["config"])

    batch = snap_svc.snapshot_all_playlists(alert_on_unavailable=True)
    res = email_svc.send_digest_notification(batch, force_all=all, provider_name=p.display_name)

    if state["json_mode"]:
        console.print_json(data=res)
        return

    if res.get("sent"):
        console.print(f"[bold green]Notification dispatched successfully ('{res.get('subject')}').[/bold green]")
    else:
        console.print(f"[dim]Notification skipped or not sent: {res.get('reason') or res.get('error')}[/dim]")


@notify_app.command("digest")
def notify_digest(
    force: bool = typer.Option(False, "--force", "-f", help="Force sending digest even if no changes detected"),
    include_followers: bool = typer.Option(True, "--followers/--no-followers", help="Include profile and playlist follower reach"),
    provider: Optional[str] = typer.Option(None, "-p", "--provider", help="Target streaming provider"),
) -> None:
    """Send unified daily library and social digest (playlist changes, alerts, followers)."""
    p = get_active_provider(provider, require_active=True)
    snap_svc = SnapshotService(p, cfg=state["config"])

    res = snap_svc.send_daily_digest(
        force=force,
        provider_name=p.display_name,
        include_followers=include_followers,
    )

    if state["json_mode"]:
        console.print_json(data=res)
        return

    if res.get("sent"):
        console.print(f"[bold green]Daily digest dispatched successfully ('{res.get('subject')}').[/bold green]")
    else:
        console.print(f"[dim]Daily digest skipped or not sent: {res.get('reason') or res.get('error')}[/dim]")


# ---------------------------------------------------------------------------
# Job Commands
# ---------------------------------------------------------------------------
@job_app.command("list")
def job_list() -> None:
    """List automated jobs declared in jobs.yaml."""
    runner = JobRunner(cfg=state["config"])
    jobs = runner.list_jobs()

    if state["json_mode"]:
        console.print(json.dumps(jobs, indent=2))
        return

    table = Table(title="Configured Automated Jobs", border_style="cyan")
    table.add_column("Job Name", style="bold")
    table.add_column("Description")

    for name, desc in jobs.items():
        table.add_row(name, desc)

    console.print(table)


@job_app.command("run")
def job_run(
    job_name: str = typer.Argument(..., help="Job identifier declared in jobs.yaml"),
    provider: Optional[str] = typer.Option(None, "-p", "--provider", help="Target streaming provider"),
    quiet: bool = typer.Option(False, "-q", "--quiet", help="Suppress console output (ideal for cron)."),
    json_mode: bool = typer.Option(False, "--json", help="Output machine-readable JSON."),
) -> None:
    """Execute an automated job action pipeline."""
    if quiet:
        state["quiet"] = True
    if json_mode:
        state["json_mode"] = True

    p = get_active_provider(provider, require_active=True)
    runner = JobRunner(provider=p, cfg=state["config"])
    try:
        res = runner.run_job(job_name)
        if state["json_mode"]:
            console.print_json(data=res)
        elif not state["quiet"]:
            console.print(f"[bold green]Job '{job_name}' completed successfully ({res['actions_executed']} action(s)).[/bold green]")
    except Exception as e:
        err_console.print(f"[bold red]Job '{job_name}' failed: {e}[/bold red]")
        raise typer.Exit(1)


@job_app.command("cron-help")
def job_cron_help(
    job_name: str = typer.Option("daily-discovery-megamix", "--job", help="Job name"),
) -> None:
    """Display ready-to-copy crontab configuration line."""
    runner = JobRunner(cfg=state["config"])
    console.print(runner.generate_cron_help(job_name=job_name))


# ---------------------------------------------------------------------------
# Favorites Commands
# ---------------------------------------------------------------------------
@favorites_app.command("list")
def favorites_list(
    limit: Optional[int] = typer.Option(None, "--limit", "-l", help="Limit number of tracks displayed on console"),
    provider: Optional[str] = typer.Option(None, "-p", "--provider", help="Target streaming provider"),
) -> None:
    """List favorite/liked tracks."""
    p = get_active_provider(provider, require_active=True)
    tracks = p.get_favorite_tracks()

    if state["json_mode"]:
        data = [
            {
                "id": str(t.id),
                "title": t.title,
                "artist": getattr(getattr(t, "artist", None), "name", "Unknown Artist"),
                "album": getattr(getattr(t, "album", None), "title", "Unknown Album"),
                "duration_seconds": t.duration_seconds,
                "isrc": t.isrc,
                "available": t.available,
            }
            for t in tracks
        ]
        console.print_json(data=data)
        return

    table = Table(title=f"{p.display_name} Favorite Tracks ({len(tracks)} total)", border_style="cyan")
    table.add_column("#", justify="right", style="dim")
    table.add_column("Title", style="bold")
    table.add_column("Artist")
    table.add_column("Album", style="dim")
    table.add_column("Duration", justify="right")
    table.add_column("ISRC", style="dim")
    table.add_column("Status")

    display_tracks = tracks[:limit] if limit and limit > 0 else tracks
    for idx, t in enumerate(display_tracks, start=1):
        dur = getattr(t, "duration_seconds", 0) or 0
        dur_str = f"{dur//60}:{dur%60:02d}"
        art = getattr(getattr(t, "artist", None), "name", "Unknown Artist")
        alb = getattr(getattr(t, "album", None), "title", "Unknown Album")
        status = "[green]Available[/green]" if getattr(t, "available", True) else "[bold red]Unavailable[/bold red]"
        table.add_row(
            str(idx),
            t.title,
            art,
            alb,
            dur_str,
            str(t.isrc or ""),
            status,
        )

    console.print(table)
    if limit and len(tracks) > limit:
        console.print(f"[dim]Showing {limit} of {len(tracks)} favorite tracks. Omit --limit to see all.[/dim]")


@favorites_app.command("snapshot")
def favorites_snapshot(
    alert: bool = typer.Option(True, "--alert/--no-alert", help="Record alerts if unavailable tracks are detected"),
    provider: Optional[str] = typer.Option(None, "-p", "--provider", help="Target streaming provider"),
) -> None:
    """Take a versioned SHA-256 snapshot of favorite/liked tracks."""
    p = get_active_provider(provider, require_active=True)
    svc = SnapshotService(p, cfg=state["config"])
    try:
        res = svc.snapshot_favorite_tracks(alert_on_unavailable=alert)
    except Exception as e:
        err_console.print(f"[bold red]Snapshot failed: {e}[/bold red]")
        raise typer.Exit(1)

    if state["json_mode"]:
        console.print_json(data=res)
        return

    table = Table(title=f"Favorites Snapshot -- {p.display_name}", border_style="green")
    table.add_column("Property", style="bold")
    table.add_column("Value")

    table.add_row("Total Favorites", str(res.get("track_count", 0)))
    table.add_row("Snapshot Version", str(res.get("version", "")))
    table.add_row("SHA-256 Checksum", str(res.get("checksum", ""))[:16] + "...")
    table.add_row("Added Since Last", f"[green]+{len(res.get('added_tracks', []))}[/green]")
    table.add_row("Removed Since Last", f"[red]-{len(res.get('removed_tracks', []))}[/red]")
    table.add_row("Unavailable Alerts", f"[yellow]{res.get('unavailable_alerts_count', 0)}[/yellow]")
    table.add_row("Snapshot Path", str(res.get("snapshot_path", "")))

    console.print(table)


@favorites_app.command("backup")
def favorites_backup(
    target: str = typer.Option("Favorites Backup", "--target", "-t", help="Target playlist title"),
    mode: str = typer.Option("replace", "--mode", "-m", help="Mode: replace (wipe target first) or append"),
    description: str = typer.Option("Automated backup of favorite/liked tracks", "--desc", "-d", help="Playlist description"),
    folder: Optional[str] = typer.Option(None, "--folder", "-f", help="Target playlist folder name or ID"),
    provider: Optional[str] = typer.Option(None, "-p", "--provider", help="Target streaming provider"),
) -> None:
    """Mirror favorite/liked tracks into a regular playlist."""
    p = get_active_provider(provider, require_active=True)
    svc = PlaylistService(p)
    try:
        res = svc.backup_favorites_to_playlist(target_playlist=target, mode=mode, description=description, folder=folder)
    except Exception as e:
        err_console.print(f"[bold red]Backup failed: {e}[/bold red]")
        raise typer.Exit(1)

    if state["json_mode"]:
        console.print_json(data=res)
        return

    folder_str = f"\n[dim]Folder: {res.get('folder')}[/dim]" if res.get("folder") else ""
    console.print(
        Panel(
            f"[bold green]Successfully mirrored {res.get('total_favorites', 0)} favorites into '{target}' on {p.display_name} (mode: {mode}).[/bold green]\n"
            f"[dim]Playlist ID: {res.get('playlist_id')}[/dim]{folder_str}",
            title="Favorites Backup Complete",
            border_style="green",
        )
    )


@favorites_app.command("export")
def favorites_export(
    format: str = typer.Option("json", "--format", "-f", help="Export format: json, m3u8, csv"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="File path to save export"),
    provider: Optional[str] = typer.Option(None, "-p", "--provider", help="Target streaming provider"),
) -> None:
    """Export favorite/liked tracks to JSON, M3U8, or CSV."""
    p = get_active_provider(provider, require_active=True)
    svc = PlaylistService(p)
    try:
        content = svc.export_favorites(export_format=format)
        if output:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(content, encoding="utf-8")
            console.print(f"[bold green]Exported favorites to {output}[/bold green]")
        else:
            console.print(content)
    except Exception as e:
        err_console.print(f"[bold red]Export failed: {e}[/bold red]")
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
