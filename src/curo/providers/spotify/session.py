"""Spotify session and OAuth authentication manager."""

from __future__ import annotations

import datetime
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import spotipy
from spotipy.oauth2 import SpotifyOAuth
from rich.console import Console
from rich.panel import Panel

from curo.config import ConfigManager, config_manager

logger = logging.getLogger(__name__)
console = Console()

SPOTIFY_DEFAULT_SCOPES = [
    "playlist-read-private",
    "playlist-read-collaborative",
    "playlist-modify-public",
    "playlist-modify-private",
    "user-library-read",
    "user-read-private",
]

DEFAULT_REDIRECT_URI = "http://127.0.0.1:8888/callback"


class SpotifyAuthenticationError(Exception):
    """Raised when Spotify authentication fails or credentials are required."""
    pass


class SpotifySessionManager:
    """Handles OAuth2 login, credential configuration, and automated token refresh for Spotify."""

    def __init__(self, cfg: Optional[ConfigManager] = None) -> None:
        self.config = cfg or config_manager
        self.session_file = self.config.get_provider_session_file("spotify")

    def get_credentials(self) -> Tuple[Optional[str], Optional[str], str]:
        """Retrieve client ID, client secret, and redirect URI from env vars or config.json."""
        client_id = os.environ.get("SPOTIFY_CLIENT_ID") or os.environ.get("CURO_SPOTIFY_CLIENT_ID")
        client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET") or os.environ.get("CURO_SPOTIFY_CLIENT_SECRET")
        redirect_uri = (
            os.environ.get("SPOTIFY_REDIRECT_URI")
            or os.environ.get("CURO_SPOTIFY_REDIRECT_URI")
        )

        if not (client_id and client_secret):
            raw = self.config.load_raw_config()
            sp_cfg = raw.get("spotify") or {}
            if not client_id:
                client_id = sp_cfg.get("client_id")
            if not client_secret:
                client_secret = sp_cfg.get("client_secret")
            if not redirect_uri:
                redirect_uri = sp_cfg.get("redirect_uri")

        return client_id, client_secret, redirect_uri or DEFAULT_REDIRECT_URI

    def save_credentials(
        self, client_id: str, client_secret: str, redirect_uri: str = DEFAULT_REDIRECT_URI
    ) -> None:
        """Persist Spotify application credentials securely to config.json."""
        raw = self.config.load_raw_config()
        raw["spotify"] = {
            "client_id": client_id.strip(),
            "client_secret": client_secret.strip(),
            "redirect_uri": redirect_uri.strip(),
            "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        self.config.save_raw_config(raw)

    def get_auth_manager(
        self,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        redirect_uri: Optional[str] = None,
        open_browser: bool = True,
    ) -> SpotifyOAuth:
        """Create and return a configured SpotifyOAuth instance."""
        c_id, c_secret, r_uri = self.get_credentials()
        final_id = client_id or c_id
        final_secret = client_secret or c_secret
        final_uri = redirect_uri or r_uri

        if not final_id or not final_secret:
            raise SpotifyAuthenticationError(
                "Spotify Client ID and Client Secret are not configured.\n"
                "Please run 'curo auth login -p spotify' or set SPOTIFY_CLIENT_ID "
                "and SPOTIFY_CLIENT_SECRET environment variables."
            )

        self.config.ensure_config_dir()
        return SpotifyOAuth(
            client_id=final_id,
            client_secret=final_secret,
            redirect_uri=final_uri,
            scope=" ".join(SPOTIFY_DEFAULT_SCOPES),
            cache_path=str(self.session_file),
            open_browser=open_browser,
        )

    def load_session(self, auto_refresh: bool = True) -> spotipy.Spotify:
        """Load an existing Spotify session from cached tokens, validating/refreshing token."""
        if not self.session_file.exists():
            raise SpotifyAuthenticationError(
                f"No Spotify session found at {self.session_file}. "
                "Please run 'curo auth login -p spotify' first."
            )

        auth_manager = self.get_auth_manager(open_browser=False)
        token_info = auth_manager.cache_handler.get_cached_token()

        if not token_info:
            raise SpotifyAuthenticationError(
                "Cached Spotify session token is missing or invalid. "
                "Please run 'curo auth login -p spotify' to authenticate."
            )

        if auto_refresh and auth_manager.is_token_expired(token_info):
            logger.info("Spotify token expired. Refreshing token silently...")
            try:
                refreshed = auth_manager.refresh_access_token(token_info["refresh_token"])
                if refreshed:
                    self.config.secure_file(self.session_file)
                    token_info = refreshed
            except Exception as e:
                raise SpotifyAuthenticationError(f"Failed to refresh Spotify token: {e}")

        sp = spotipy.Spotify(auth_manager=auth_manager)
        try:
            sp.current_user()
            self.config.secure_file(self.session_file)
            return sp
        except Exception as e:
            raise SpotifyAuthenticationError(f"Spotify authentication verification failed: {e}")

    def login_interactive(self) -> spotipy.Spotify:
        """Interactive authorization flow for Spotify OAuth2."""
        self.config.ensure_config_dir()
        client_id, client_secret, redirect_uri = self.get_credentials()

        if not client_id or not client_secret:
            console.print(
                Panel(
                    "[bold cyan]Spotify Developer Credentials Setup[/bold cyan]\n\n"
                    "To connect Spotify with Musicae Curo, you need a free Spotify Developer App:\n"
                    " 1. Go to [bold underline]https://developer.spotify.com/dashboard[/bold underline]\n"
                    " 2. Create an App (e.g. named 'Musicae Curo')\n"
                    f" 3. In App Settings, add Redirect URI: [bold green]{DEFAULT_REDIRECT_URI}[/bold green]\n"
                    " 4. Copy your Client ID and Client Secret.",
                    title="[bold]Spotify Setup[/bold]",
                    border_style="cyan",
                )
            )
            import typer
            prompt_id = typer.prompt("Spotify Client ID").strip()
            prompt_secret = typer.prompt("Spotify Client Secret", hide_input=True).strip()
            if not prompt_id or not prompt_secret:
                raise SpotifyAuthenticationError("Client ID and Client Secret are required.")
            self.save_credentials(prompt_id, prompt_secret, redirect_uri)
            client_id, client_secret = prompt_id, prompt_secret

        auth_manager = self.get_auth_manager(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
            open_browser=True,
        )

        auth_url = auth_manager.get_authorize_url()
        msg = (
            f"[bold green]To authorize Spotify with Musicae Curo, visit:[/bold green]\n"
            f"[link={auth_url}]{auth_url}[/link]\n\n"
            f"Redirect URI: [bold cyan]{redirect_uri}[/bold cyan]\n\n"
            f"[dim]A browser window will open automatically. If running headlessly, "
            f"copy the callback URL after login.[/dim]"
        )
        console.print(Panel(msg, title="[bold]Spotify OAuth Login[/bold]", border_style="cyan"))

        # get_access_token handles local callback server or user URL prompt
        token_info = auth_manager.get_access_token(as_dict=True)
        if not token_info:
            raise SpotifyAuthenticationError("Authorization was cancelled or did not return tokens.")

        self.config.secure_file(self.session_file)
        sp = spotipy.Spotify(auth_manager=auth_manager)
        user = sp.current_user()
        name = user.get("display_name") or user.get("id") or "Spotify User"
        console.print(
            f"[bold green]Successfully authenticated with Spotify as {name} (ID: {user.get('id')})![/bold green]"
        )
        return sp

    def logout(self) -> bool:
        """Remove stored session cache file."""
        if self.session_file.exists():
            self.session_file.unlink()
            return True
        return False

    def get_status(self) -> Dict[str, Any]:
        """Check session status without throwing exceptions."""
        if not self.session_file.exists():
            return {
                "logged_in": False,
                "session_file": str(self.session_file),
                "error": "No session file found",
            }

        try:
            sp = self.load_session(auto_refresh=True)
            u = sp.current_user() or {}
            name = u.get("display_name") or u.get("id") or "Unknown"

            return {
                "logged_in": True,
                "user_id": u.get("id"),
                "user_name": name,
                "country_code": u.get("country"),
                "session_file": str(self.session_file),
            }
        except Exception as e:
            return {
                "logged_in": False,
                "session_file": str(self.session_file),
                "error": str(e),
            }

