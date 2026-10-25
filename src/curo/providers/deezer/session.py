"""Deezer session and OAuth authentication manager."""

from __future__ import annotations

import datetime
import http.server
import json
import logging
import os
import urllib.parse
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import deezer
import httpx
from rich.console import Console
from rich.panel import Panel

from curo.config import ConfigManager, config_manager

logger = logging.getLogger(__name__)
console = Console()

DEFAULT_REDIRECT_URI = "http://127.0.0.1:8888/callback"
DEEZER_PERMS = "basic_access,email,offline_access,manage_library,delete_library"


class DeezerAuthenticationError(Exception):
    """Raised when Deezer authentication fails or credentials are required."""
    pass


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    """Temporary local HTTP server to capture OAuth authorization code."""

    auth_code: Optional[str] = None
    error: Optional[str] = None

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        if "code" in params:
            _CallbackHandler.auth_code = params["code"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                b"<html><body><h1>Musicae Curo</h1><p>Authorization successful! You can return to your terminal.</p></body></html>"
            )
        elif "error_reason" in params or "error" in params:
            _CallbackHandler.error = params.get("error_reason", params.get("error", ["Unknown error"]))[0]
            self.send_response(400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"<html><body><h1>Authorization Failed</h1><p>Access was denied or encountered an error.</p></body></html>")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        pass


class DeezerSessionManager:
    """Handles OAuth2 login, API token persistence, and session verification for Deezer."""

    def __init__(self, cfg: Optional[ConfigManager] = None) -> None:
        self.config = cfg or config_manager
        self.session_file = self.config.get_provider_session_file("deezer")

    def get_credentials(self) -> Tuple[Optional[str], Optional[str], Optional[str], str]:
        """Retrieve access token, app ID, secret, and redirect URI from env vars or config.json."""
        access_token = (
            os.environ.get("DEEZER_ACCESS_TOKEN")
            or os.environ.get("CURO_DEEZER_ACCESS_TOKEN")
        )
        app_id = (
            os.environ.get("DEEZER_APP_ID")
            or os.environ.get("CURO_DEEZER_APP_ID")
        )
        app_secret = (
            os.environ.get("DEEZER_APP_SECRET")
            or os.environ.get("CURO_DEEZER_APP_SECRET")
        )
        redirect_uri = (
            os.environ.get("DEEZER_REDIRECT_URI")
            or os.environ.get("CURO_DEEZER_REDIRECT_URI")
        )

        raw = self.config.load_raw_config()
        dz_cfg = raw.get("deezer") or {}

        if not access_token:
            access_token = dz_cfg.get("access_token")
        if not access_token and self.session_file.exists():
            try:
                with open(self.session_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    access_token = data.get("access_token")
            except Exception:
                pass
        if not app_id:
            app_id = dz_cfg.get("app_id")
        if not app_secret:
            app_secret = dz_cfg.get("app_secret")
        if not redirect_uri:
            redirect_uri = dz_cfg.get("redirect_uri")

        return access_token, app_id, app_secret, redirect_uri or DEFAULT_REDIRECT_URI

    def save_session_data(
        self,
        access_token: str,
        user_info: Optional[Dict[str, Any]] = None,
        app_id: Optional[str] = None,
        app_secret: Optional[str] = None,
    ) -> None:
        """Save access token and session metadata to session file and config.json."""
        self.config.ensure_config_dir()
        data = {
            "access_token": access_token.strip(),
            "user": user_info or {},
            "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }

        with open(self.session_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        self.config.secure_file(self.session_file)

        # Also store app credentials in config.json if provided
        if app_id or app_secret:
            raw = self.config.load_raw_config()
            dz_cfg = raw.get("deezer") or {}
            if app_id:
                dz_cfg["app_id"] = str(app_id).strip()
            if app_secret:
                dz_cfg["app_secret"] = str(app_secret).strip()
            raw["deezer"] = dz_cfg
            self.config.save_raw_config(raw)

    def load_session(self) -> deezer.Client:
        """Load an authenticated Deezer client from cached session file or environment variable."""
        token: Optional[str] = None

        if self.session_file.exists():
            try:
                with open(self.session_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    token = data.get("access_token")
            except Exception as e:
                logger.warning(f"Failed to read Deezer session file: {e}")

        if not token:
            token, _, _, _ = self.get_credentials()

        if not token:
            raise DeezerAuthenticationError(
                f"No Deezer session found at {self.session_file}.\n"
                "Please run 'curo auth login -p deezer' first."
            )

        client = deezer.Client(access_token=token)
        try:
            client.get_user("me")
            return client
        except Exception as e:
            raise DeezerAuthenticationError(f"Deezer session token verification failed: {e}")

    def login_interactive(self) -> deezer.Client:
        """Run interactive authentication flow via OAuth or direct access token entry."""
        self.config.ensure_config_dir()
        existing_token, app_id, app_secret, redirect_uri = self.get_credentials()

        import typer

        console.print(
            Panel(
                "[bold cyan]Deezer Authentication[/bold cyan]\n\n"
                "Deezer supports two ways to authenticate with Musicae Curo:\n"
                " 1. [bold green]OAuth 2.0 Web Authorization[/bold green] (Requires a free Deezer App ID & Secret)\n"
                " 2. [bold green]Direct Access Token / ARL[/bold green] (Quick copy-paste from browser session)",
                title="[bold]Deezer Setup[/bold]",
                border_style="cyan",
            )
        )

        choice = typer.prompt(
            "Choose authentication method:\n"
            " [1] Direct Access Token (fastest)\n"
            " [2] OAuth 2.0 Flow (Deezer Developer App)\n"
            "Choice",
            default="1",
        ).strip()

        if choice == "1":
            token_input = typer.prompt("Enter your Deezer Access Token", hide_input=True).strip()
            if not token_input:
                raise DeezerAuthenticationError("Access token is required.")

            client = deezer.Client(access_token=token_input)
            try:
                user = client.get_user("me")
                user_info = {
                    "id": getattr(user, "id", None),
                    "name": getattr(user, "name", None),
                    "country": getattr(user, "country", None),
                }
                self.save_session_data(token_input, user_info=user_info)
                console.print(
                    f"[bold green]Successfully authenticated with Deezer as {user_info['name']} (ID: {user_info['id']})![/bold green]"
                )
                return client
            except Exception as e:
                raise DeezerAuthenticationError(f"Invalid Deezer access token: {e}")

        # Choice 2: OAuth Flow
        if not app_id or not app_secret:
            console.print(
                Panel(
                    "To create a free Deezer Developer App:\n"
                    " 1. Go to [bold underline]https://developers.deezer.com/myapps[/bold underline]\n"
                    " 2. Create an Application\n"
                    f" 3. Set Redirect URL domain to: [bold green]http://127.0.0.1:8888/callback[/bold green]\n"
                    " 4. Copy your Application ID and Secret Key.",
                    title="[bold]Deezer App Setup[/bold]",
                    border_style="cyan",
                )
            )
            app_id = typer.prompt("Deezer Application ID").strip()
            app_secret = typer.prompt("Deezer Secret Key", hide_input=True).strip()

        if not app_id or not app_secret:
            raise DeezerAuthenticationError("App ID and Secret Key are required for OAuth.")

        auth_url = (
            f"https://connect.deezer.com/oauth/auth.php?"
            f"app_id={app_id}&redirect_uri={urllib.parse.quote(redirect_uri)}&perms={DEEZER_PERMS}"
        )

        msg = (
            f"[bold green]To authorize Deezer with Musicae Curo, visit:[/bold green]\n"
            f"[link={auth_url}]{auth_url}[/link]\n\n"
            f"Redirect URI: [bold cyan]{redirect_uri}[/bold cyan]\n\n"
            f"[dim]A browser window will open automatically. If running headlessly, "
            f"approve access and paste the redirected callback URL or code below.[/dim]"
        )
        console.print(Panel(msg, title="[bold]Deezer OAuth Login[/bold]", border_style="cyan"))

        # Attempt to run local callback server on port 8888
        code = None
        server = None
        parsed_uri = urllib.parse.urlparse(redirect_uri)
        port = parsed_uri.port or 8888

        try:
            _CallbackHandler.auth_code = None
            _CallbackHandler.error = None
            server = http.server.HTTPServer(("127.0.0.1", port), _CallbackHandler)
            server.timeout = 60.0

            import webbrowser
            try:
                webbrowser.open(auth_url)
            except Exception:
                pass

            console.print("[dim]Waiting for authorization callback on http://127.0.0.1:8888/callback (timeout: 60s)...[/dim]")
            server.handle_request()

            if _CallbackHandler.auth_code:
                code = _CallbackHandler.auth_code
            elif _CallbackHandler.error:
                raise DeezerAuthenticationError(f"OAuth error: {_CallbackHandler.error}")
        except Exception as e:
            logger.debug(f"Local callback listener not available: {e}")
        finally:
            if server:
                server.server_close()

        if not code:
            code_input = typer.prompt("Enter the 'code' parameter or full callback URL").strip()
            if "code=" in code_input:
                parsed_q = urllib.parse.parse_qs(urllib.parse.urlparse(code_input).query)
                code = parsed_q.get("code", [None])[0]
            else:
                code = code_input

        if not code:
            raise DeezerAuthenticationError("No authorization code received.")

        # Exchange code for access token
        token_url = (
            f"https://connect.deezer.com/oauth/access_token.php?"
            f"app_id={app_id}&secret={app_secret}&code={code}&output=json"
        )

        with httpx.Client() as http_client:
            resp = http_client.get(token_url)
            if resp.status_code != 200:
                raise DeezerAuthenticationError(f"Failed to exchange OAuth code: HTTP {resp.status_code}")

            try:
                tok_data = resp.json()
            except Exception:
                # Deezer sometimes returns query string format: access_token=...&expires=0
                tok_data = dict(urllib.parse.parse_qsl(resp.text))

        access_token = tok_data.get("access_token")
        if not access_token:
            err = tok_data.get("error", "Unknown token exchange failure")
            raise DeezerAuthenticationError(f"Deezer token exchange failed: {err}")

        client = deezer.Client(access_token=access_token)
        user = client.get_user("me")
        user_info = {
            "id": getattr(user, "id", None),
            "name": getattr(user, "name", None),
            "country": getattr(user, "country", None),
        }

        self.save_session_data(
            access_token=access_token,
            user_info=user_info,
            app_id=app_id,
            app_secret=app_secret,
        )

        console.print(
            f"[bold green]Successfully authenticated with Deezer as {user_info['name']} (ID: {user_info['id']})![/bold green]"
        )
        return client

    def logout(self) -> bool:
        """Remove stored Deezer session file."""
        if self.session_file.exists():
            self.session_file.unlink()
            return True
        return False

    def get_status(self) -> Dict[str, Any]:
        """Check session status without throwing exceptions."""
        if not self.session_file.exists():
            # Check env var fallback
            tok, _, _, _ = self.get_credentials()
            if not tok:
                return {
                    "logged_in": False,
                    "session_file": str(self.session_file),
                    "error": "No session file found",
                }

        try:
            client = self.load_session()
            user = client.get_user("me")
            return {
                "logged_in": True,
                "user_id": getattr(user, "id", None),
                "user_name": getattr(user, "name", "Deezer User"),
                "country_code": getattr(user, "country", None),
                "session_file": str(self.session_file),
            }
        except Exception as e:
            return {
                "logged_in": False,
                "session_file": str(self.session_file),
                "error": str(e),
            }