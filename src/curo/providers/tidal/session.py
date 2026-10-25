"""Tidal session and OAuth authentication manager."""

from __future__ import annotations

import datetime
import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

import tidalapi
from rich.console import Console
from rich.panel import Panel

from curo.config import ConfigManager, config_manager

logger = logging.getLogger(__name__)
console = Console()


class TidalAuthenticationError(Exception):
    """Raised when Tidal authentication fails or credentials are required."""
    pass


class TidalSessionManager:
    """Handles OAuth2 login, persistence, and automated headless token refresh for Tidal."""

    def __init__(self, cfg: Optional[ConfigManager] = None) -> None:
        self.config = cfg or config_manager
        self.session_file = self.config.get_provider_session_file("tidal")

    def load_session(self, auto_refresh: bool = True) -> tidalapi.Session:
        """Load an existing session from session file, refreshing token if expired."""
        if not self.session_file.exists():
            raise TidalAuthenticationError(
                f"No session file found at {self.session_file}. "
                "Please run 'curo auth login -p tidal' first."
            )

        session = tidalapi.Session()
        try:
            with open(self.session_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            raise TidalAuthenticationError(f"Failed to read session file {self.session_file}: {e}")

        token_type = data.get("token_type", {}).get("data")
        access_token = data.get("access_token", {}).get("data")
        refresh_token = data.get("refresh_token", {}).get("data")
        is_pkce = data.get("is_pkce", {}).get("data", False)

        if not access_token:
            raise TidalAuthenticationError("Session file is corrupt: missing access token.")

        loaded = session.load_oauth_session(
            token_type=token_type,
            access_token=access_token,
            refresh_token=refresh_token,
            is_pkce=is_pkce,
        )

        if loaded:
            try:
                if session.check_login():
                    return session
            except Exception:
                pass

        if auto_refresh and refresh_token:
            logger.info("Access token expired or session invalid. Attempting silent token refresh...")
            try:
                refreshed = session.token_refresh(refresh_token)
                if refreshed:
                    if session.load_oauth_session(
                        token_type=session.token_type,
                        access_token=session.access_token,
                        refresh_token=refresh_token,
                        is_pkce=is_pkce,
                    ) and session.check_login():
                        self.save_session(session)
                        logger.info("Token refreshed successfully and session saved.")
                        return session
            except Exception as e:
                logger.warning(f"Silent token refresh failed: {e}")

        raise TidalAuthenticationError(
            "Tidal session has expired and could not be automatically refreshed. "
            "Please run 'curo auth login -p tidal' to re-authenticate."
        )

    def save_session(self, session: tidalapi.Session) -> None:
        """Save active session to session file with strict 0600 file permissions."""
        self.config.ensure_config_dir()
        data = {
            "token_type": {"data": session.token_type},
            "session_id": {"data": session.session_id},
            "access_token": {"data": session.access_token},
            "refresh_token": {"data": session.refresh_token},
            "is_pkce": {"data": session.is_pkce},
            "user_id": {"data": session.user.id if session.user else None},
            "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }

        with open(self.session_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

        self.config.secure_file(self.session_file)

    def login_interactive(self) -> tidalapi.Session:
        """Run interactive OAuth2 Device Authorization flow."""
        self.config.ensure_config_dir()
        session = tidalapi.Session()

        login, future = session.login_oauth()
        verification_url = f"https://{login.verification_uri_complete}"
        user_code = getattr(login, "user_code", "N/A")

        msg = (
            f"[bold green]To authorize Tidal with Musicae Curo, visit:[/bold green]\n"
            f"[link={verification_url}]{verification_url}[/link]\n\n"
            f"Code: [bold cyan]{user_code}[/bold cyan] (expires in {login.expires_in}s)\n\n"
            f"[dim]Waiting for authorization in your browser or phone...[/dim]"
        )
        console.print(Panel(msg, title="[bold]Tidal Device Login[/bold]", border_style="cyan"))

        future.result()

        if not session.check_login():
            raise TidalAuthenticationError("Authorization timed out or was rejected.")

        self.save_session(session)

        user_name = getattr(session.user, "first_name", "") or getattr(session.user, "username", "User")
        console.print(
            f"[bold green]Successfully authenticated as {user_name} (ID: {session.user.id})![/bold green]"
        )
        return session

    def logout(self) -> bool:
        """Remove stored session file."""
        if self.session_file.exists():
            self.session_file.unlink()
            return True
        return False

    def get_status(self) -> Dict[str, Any]:
        """Check session status without raising exceptions."""
        if not self.session_file.exists():
            return {
                "logged_in": False,
                "session_file": str(self.session_file),
                "error": "No session file found",
            }

        try:
            session = self.load_session(auto_refresh=True)
            u = session.user
            name = f"{getattr(u, 'first_name', '')} {getattr(u, 'last_name', '')}".strip()
            if not name:
                name = getattr(u, "username", "Unknown")

            return {
                "logged_in": True,
                "user_id": u.id,
                "user_name": name,
                "country_code": session.country_code,
                "session_file": str(self.session_file),
            }
        except Exception as e:
            return {
                "logged_in": False,
                "session_file": str(self.session_file),
                "error": str(e),
            }

