"""Email notification service for playlist change digests and availability alerts."""

from __future__ import annotations

import datetime
import html
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Dict, List, Optional

from tidal_cli.config import ConfigManager, EmailConfig, config_manager

logger = logging.getLogger("tidal_cli.email_service")


class EmailService:
    """Renders and dispatches elegant HTML/plain-text email notifications via SMTP."""

    def __init__(self, cfg: Optional[ConfigManager] = None) -> None:
        self.config = cfg or config_manager
        self.email_config = self.config.get_email_config()

    def send_email(
        self,
        subject: str,
        html_body: str,
        text_body: str,
        recipients: Optional[List[str]] = None,
    ) -> bool:
        """Send a multipart email via configured SMTP server."""
        if not self.email_config.is_configured:
            logger.info("Email notifications not configured (smtp_host or to_address missing).")
            return False

        sender = self.email_config.from_address or self.email_config.smtp_user or "tidal-cli@localhost"
        target_recipients = recipients or [r.strip() for r in self.email_config.to_address.split(",") if r.strip()]

        if not target_recipients:
            logger.warning("No valid recipient email address found.")
            return False

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = sender
        msg["To"] = ", ".join(target_recipients)
        msg["Date"] = datetime.datetime.now(datetime.timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")

        # Attach text part first (fallback), then HTML part (preferred)
        msg.attach(MIMEText(text_body, "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        host = self.email_config.smtp_host
        port = self.email_config.smtp_port

        logger.info(f"Connecting to SMTP server at {host}:{port}...")

        try:
            if self.email_config.use_ssl:
                server = smtplib.SMTP_SSL(host, port, timeout=30)
            else:
                server = smtplib.SMTP(host, port, timeout=30)

            with server:
                server.ehlo()
                if self.email_config.use_tls and not self.email_config.use_ssl:
                    server.starttls()
                    server.ehlo()

                if self.email_config.smtp_user and self.email_config.smtp_password:
                    server.login(self.email_config.smtp_user, self.email_config.smtp_password)

                server.sendmail(sender, target_recipients, msg.as_string())
                logger.info(f"Notification email successfully sent to {target_recipients} ('{subject}')")
                return True

        except Exception as e:
            logger.error(f"Failed to send email notification: {e}", exc_info=True)
            raise

    def send_test_email(self) -> Dict[str, Any]:
        """Send a test email to verify SMTP configuration and connectivity."""
        if not self.email_config.is_configured:
            return {
                "success": False,
                "error": "Incomplete configuration. Please specify at least smtp_host and to_address.",
            }

        subject = "[TIDAL-CLI] Test Notification Email"
        now_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        html_body = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f8fafc; color: #1e293b; margin: 0; padding: 24px; }}
    .card {{ background-color: #ffffff; border: 1px solid #e2e8f0; border-radius: 8px; max-width: 560px; margin: 0 auto; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.05); }}
    .header {{ background-color: #0f172a; border-bottom: 3px solid #0ea5e9; color: #ffffff; padding: 20px 24px; }}
    .header h2 {{ margin: 0; font-size: 20px; font-weight: 600; }}
    .content {{ padding: 24px; line-height: 1.5; font-size: 14px; }}
    .badge {{ display: inline-block; background-color: #ecfdf5; color: #047857; border: 1px solid #a7f3d0; border-radius: 4px; padding: 4px 8px; font-size: 12px; font-weight: 600; margin-bottom: 16px; }}
    .footer {{ background-color: #f8fafc; border-top: 1px solid #e2e8f0; padding: 12px 24px; font-size: 12px; color: #64748b; }}
  </style>
</head>
<body>
  <div class="card">
    <div class="header">
      <h2>TIDAL-CLI Notification Setup</h2>
    </div>
    <div class="content">
      <div class="badge">Connection Successful</div>
      <p>Your SMTP email notification settings are working properly.</p>
      <p><strong>Host:</strong> {html.escape(self.email_config.smtp_host or '')}<br>
         <strong>Port:</strong> {self.email_config.smtp_port}<br>
         <strong>Sender:</strong> {html.escape(self.email_config.from_address or self.email_config.smtp_user or '')}<br>
         <strong>Recipient:</strong> {html.escape(self.email_config.to_address or '')}<br>
         <strong>Timestamp:</strong> {now_str}</p>
      <p style="color: #64748b;">You will receive automated digests whenever playlists are modified or when tracks are delisted by Tidal.</p>
    </div>
    <div class="footer">
      Generated automatically by tidal-cli
    </div>
  </div>
</body>
</html>"""

        text_body = f"""TIDAL-CLI Notification Setup - Test Email
========================================

Your SMTP notification settings are working properly.

Host: {self.email_config.smtp_host}:{self.email_config.smtp_port}
Sender: {self.email_config.from_address or self.email_config.smtp_user}
Recipient: {self.email_config.to_address}
Timestamp: {now_str}

You will receive automated digests whenever playlists are modified or when tracks are delisted by Tidal.
"""

        try:
            sent = self.send_email(subject, html_body, text_body)
            return {"success": sent}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ---------------------------------------------------------------------------
    # Template 1: All Playlists Baseline Overview (First Run & Manual Trigger)
    # ---------------------------------------------------------------------------
    def render_all_playlists_report(
        self,
        playlists: List[Dict[str, Any]],
        recent_alerts: Optional[List[Dict[str, Any]]] = None,
    ) -> tuple[str, str]:
        """Render the complete all-playlists overview email (used for first run and manual requests)."""
        now_str = datetime.datetime.now(datetime.timezone.utc).strftime("%B %d, %Y - %H:%M UTC")
        total_playlists = len(playlists)
        total_tracks = sum(p.get("track_count", p.get("total_tracks", 0)) for p in playlists if "error" not in p)
        total_unavail = sum(p.get("unavailable_tracks_count", p.get("unavailable_count", 0)) for p in playlists if "error" not in p)
        healthy_tracks = total_tracks - total_unavail

        status_badge = (
            '<span style="background-color: #ecfdf5; color: #047857; border: 1px solid #a7f3d0; border-radius: 4px; padding: 4px 10px; font-size: 13px; font-weight: 600;">All Tracks Playable</span>'
            if total_unavail == 0
            else f'<span style="background-color: #fef2f2; color: #b91c1c; border: 1px solid #fecaca; border-radius: 4px; padding: 4px 10px; font-size: 13px; font-weight: 600;">{total_unavail} Unavailable Track(s) Detected</span>'
        )

        rows_html = []
        for pl in playlists:
            if "error" in pl:
                rows_html.append(f"""
                <tr>
                  <td style="padding: 10px 12px; border-bottom: 1px solid #e2e8f0; font-weight: 600;">{html.escape(pl.get("playlist_name", "Unknown"))}</td>
                  <td style="padding: 10px 12px; border-bottom: 1px solid #e2e8f0; text-align: center; color: #ef4444;" colspan="3">Error: {html.escape(str(pl.get("error")))}</td>
                </tr>
                """)
                continue

            name = pl.get("name", pl.get("playlist_name", "Unknown"))
            t_count = pl.get("track_count", pl.get("total_tracks", 0))
            unavail = pl.get("unavailable_tracks_count", pl.get("unavailable_count", 0))
            version = pl.get("version", pl.get("version_count", 1))

            unavail_display = (
                '<span style="color: #10b981; font-weight: 600;">0</span>'
                if unavail == 0
                else f'<span style="color: #ef4444; font-weight: 700;">{unavail}</span>'
            )

            rows_html.append(f"""
            <tr>
              <td style="padding: 10px 12px; border-bottom: 1px solid #e2e8f0; font-weight: 600; color: #0f172a;">{html.escape(name)}</td>
              <td style="padding: 10px 12px; border-bottom: 1px solid #e2e8f0; text-align: right; color: #475569;">v{version}</td>
              <td style="padding: 10px 12px; border-bottom: 1px solid #e2e8f0; text-align: right; color: #475569;">{t_count}</td>
              <td style="padding: 10px 12px; border-bottom: 1px solid #e2e8f0; text-align: right;">{unavail_display}</td>
            </tr>
            """)

        # Gather any delisted tracks across playlists
        delisted_items = []
        for pl in playlists:
            unavail_list = pl.get("unavailable_tracks", [])
            for ut in unavail_list:
                delisted_items.append({
                    "playlist": pl.get("name", pl.get("playlist_name", "")),
                    "title": ut.get("title", ""),
                    "artist": ut.get("artist", ""),
                    "album": ut.get("album", ""),
                    "has_recovery": bool(ut.get("historical_isrc") or ut.get("historical_upc")),
                })

        delisted_html = ""
        if delisted_items:
            delisted_rows = []
            for d in delisted_items:
                recovery_badge = (
                    '<span style="color: #0284c7; font-weight: 600;">ISRC/UPC Cached</span>'
                    if d["has_recovery"]
                    else '<span style="color: #94a3b8;">None</span>'
                )
                delisted_rows.append(f"""
                <tr>
                  <td style="padding: 8px 10px; border-bottom: 1px solid #f1f5f9; font-weight: 500;">{html.escape(d['title'])}</td>
                  <td style="padding: 8px 10px; border-bottom: 1px solid #f1f5f9; color: #475569;">{html.escape(d['artist'])}</td>
                  <td style="padding: 8px 10px; border-bottom: 1px solid #f1f5f9; color: #64748b; font-size: 12px;">{html.escape(d['playlist'])}</td>
                  <td style="padding: 8px 10px; border-bottom: 1px solid #f1f5f9; text-align: right; font-size: 12px;">{recovery_badge}</td>
                </tr>
                """)

            delisted_html = f"""
            <div style="margin-top: 24px; background-color: #fff1f2; border: 1px solid #fecdd3; border-radius: 6px; padding: 16px;">
              <h3 style="margin: 0 0 10px 0; font-size: 15px; color: #9f1239;">Delisted Tracks Requiring Attention ({len(delisted_items)})</h3>
              <p style="margin: 0 0 12px 0; font-size: 13px; color: #881337;">
                The following tracks are currently unavailable on Tidal. You can run <code>tidal-cli playlist repair &lt;playlist&gt;</code> to evaluate replacement candidates.
              </p>
              <table style="width: 100%; border-collapse: collapse; font-size: 13px; background-color: #ffffff; border-radius: 4px;">
                <thead>
                  <tr style="background-color: #ffe4e6; text-align: left; color: #9f1239;">
                    <th style="padding: 8px 10px; border-bottom: 1px solid #fecdd3;">Track Title</th>
                    <th style="padding: 8px 10px; border-bottom: 1px solid #fecdd3;">Artist</th>
                    <th style="padding: 8px 10px; border-bottom: 1px solid #fecdd3;">Playlist</th>
                    <th style="padding: 8px 10px; border-bottom: 1px solid #fecdd3; text-align: right;">History Cache</th>
                  </tr>
                </thead>
                <tbody>
                  {''.join(delisted_rows)}
                </tbody>
              </table>
            </div>
            """

        html_body = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>TIDAL Catalog Snapshot</title>
</head>
<body style="margin: 0; padding: 24px 12px; background-color: #f1f5f9; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color: #1e293b;">
  <div style="max-width: 640px; margin: 0 auto; background-color: #ffffff; border-radius: 8px; border: 1px solid #cbd5e1; overflow: hidden; box-shadow: 0 2px 6px rgba(0,0,0,0.04);">
    
    <!-- Header -->
    <div style="background-color: #0f172a; padding: 24px 28px; border-bottom: 3px solid #00E8E2; color: #ffffff;">
      <h1 style="margin: 0; font-size: 20px; font-weight: 600; letter-spacing: -0.02em;">TIDAL Catalog Baseline Overview</h1>
      <p style="margin: 6px 0 0 0; font-size: 13px; color: #94a3b8;">{now_str} • Automated Snapshot Inventory</p>
    </div>

    <!-- Executive Summary Metrics -->
    <div style="padding: 24px 28px; background-color: #f8fafc; border-bottom: 1px solid #e2e8f0;">
      <div style="margin-bottom: 16px;">
        {status_badge}
      </div>
      <table style="width: 100%; border-collapse: collapse;">
        <tr>
          <td style="width: 33%; padding: 12px; background-color: #ffffff; border: 1px solid #e2e8f0; border-radius: 6px; text-align: center;">
            <div style="font-size: 12px; color: #64748b; font-weight: 500; text-transform: uppercase;">Playlists</div>
            <div style="font-size: 22px; font-weight: 700; color: #0f172a; margin-top: 4px;">{total_playlists}</div>
          </td>
          <td style="width: 2%;"></td>
          <td style="width: 32%; padding: 12px; background-color: #ffffff; border: 1px solid #e2e8f0; border-radius: 6px; text-align: center;">
            <div style="font-size: 12px; color: #64748b; font-weight: 500; text-transform: uppercase;">Total Tracks</div>
            <div style="font-size: 22px; font-weight: 700; color: #0f172a; margin-top: 4px;">{total_tracks}</div>
          </td>
          <td style="width: 2%;"></td>
          <td style="width: 33%; padding: 12px; background-color: #ffffff; border: 1px solid #e2e8f0; border-radius: 6px; text-align: center;">
            <div style="font-size: 12px; color: #64748b; font-weight: 500; text-transform: uppercase;">Unavailable</div>
            <div style="font-size: 22px; font-weight: 700; color: {'#ef4444' if total_unavail > 0 else '#10b981'}; margin-top: 4px;">{total_unavail}</div>
          </td>
        </tr>
      </table>
    </div>

    <!-- Playlists Table -->
    <div style="padding: 24px 28px;">
      <h2 style="margin: 0 0 14px 0; font-size: 16px; font-weight: 600; color: #0f172a;">Personal Playlists</h2>
      <table style="width: 100%; border-collapse: collapse; font-size: 14px;">
        <thead>
          <tr style="background-color: #f1f5f9; text-align: left; color: #475569; font-size: 12px; text-transform: uppercase; letter-spacing: 0.03em;">
            <th style="padding: 10px 12px; border-bottom: 2px solid #cbd5e1;">Playlist Name</th>
            <th style="padding: 10px 12px; border-bottom: 2px solid #cbd5e1; text-align: right;">Ver.</th>
            <th style="padding: 10px 12px; border-bottom: 2px solid #cbd5e1; text-align: right;">Tracks</th>
            <th style="padding: 10px 12px; border-bottom: 2px solid #cbd5e1; text-align: right;">Delisted</th>
          </tr>
        </thead>
        <tbody>
          {''.join(rows_html)}
        </tbody>
      </table>

      {delisted_html}
    </div>

    <!-- Footer -->
    <div style="background-color: #f8fafc; border-top: 1px solid #e2e8f0; padding: 16px 28px; font-size: 12px; color: #64748b; text-align: center;">
      Versioned snapshots and historical track identifiers stored in <code>~/.config/tidal-cli/snapshots/</code>.<br>
      Automated daily notification by <strong>tidal-cli</strong>.
    </div>

  </div>
</body>
</html>"""

        # Plain text version
        lines = [
            "TIDAL Catalog Baseline Overview",
            "=" * 32,
            f"Date: {now_str}",
            f"Total Playlists: {total_playlists} | Total Tracks: {total_tracks} | Unavailable: {total_unavail}",
            "",
            "Playlists:",
            "-" * 60,
            f"{'Playlist Name':<35} {'Ver':<6} {'Tracks':<8} {'Delisted':<8}",
            "-" * 60,
        ]
        for pl in playlists:
            name = pl.get("name", pl.get("playlist_name", "Unknown"))[:33]
            v = f"v{pl.get('version', pl.get('version_count', 1))}"
            tc = str(pl.get("track_count", pl.get("total_tracks", 0)))
            un = str(pl.get("unavailable_tracks_count", pl.get("unavailable_count", 0)))
            lines.append(f"{name:<35} {v:<6} {tc:<8} {un:<8}")

        if delisted_items:
            lines.extend([
                "",
                f"Delisted Tracks Requiring Attention ({len(delisted_items)}):",
                "-" * 60,
            ])
            for d in delisted_items:
                cache_status = "ISRC/UPC Cached" if d["has_recovery"] else "No Cache"
                lines.append(f"- {d['title']} by {d['artist']} (in '{d['playlist']}') [{cache_status}]")

        lines.extend([
            "",
            "--",
            "Automated daily notification by tidal-cli",
        ])

        return html_body, "\n".join(lines)

    # ---------------------------------------------------------------------------
    # Template 2: Changes Digest & Availability Report
    # ---------------------------------------------------------------------------
    def render_changes_digest(
        self,
        modified_playlists: List[Dict[str, Any]],
        all_playlists_summary: Optional[List[Dict[str, Any]]] = None,
        new_alerts: Optional[List[Dict[str, Any]]] = None,
    ) -> tuple[str, str]:
        """Render the email digest when one or more playlists were modified or delisted tracks detected."""
        now_str = datetime.datetime.now(datetime.timezone.utc).strftime("%B %d, %Y - %H:%M UTC")

        total_added = sum(p.get("added_count", len(p.get("diff", {}).get("added", []))) for p in modified_playlists)
        total_removed = sum(p.get("removed_count", len(p.get("diff", {}).get("removed", []))) for p in modified_playlists)
        total_unavail_alerts = sum(p.get("new_alerts_count", 0) for p in modified_playlists)
        if new_alerts:
            total_unavail_alerts = max(total_unavail_alerts, len(new_alerts))

        modified_blocks_html = []
        for pl in modified_playlists:
            name = pl.get("name", pl.get("playlist_name", "Unknown"))
            ver = pl.get("version", pl.get("version_count", 1))
            prev_ver = pl.get("prev_version", max(1, ver - 1))
            diff = pl.get("diff", {}) or {}
            added = diff.get("added", [])
            removed = diff.get("removed", [])
            unavail_count = pl.get("unavailable_tracks_count", pl.get("unavailable_count", 0))

            added_html = ""
            if added:
                added_items = "".join(
                    f'<li style="margin: 4px 0;"><strong style="color: #0f172a;">{html.escape(t["title"])}</strong> <span style="color: #64748b;">— {html.escape(t["artist"])}</span></li>'
                    for t in added[:10]
                )
                if len(added) > 10:
                    added_items += f'<li style="color: #64748b; font-style: italic;">...and {len(added) - 10} more added tracks</li>'
                added_html = f"""
                <div style="margin-top: 10px;">
                  <span style="background-color: #ecfdf5; color: #047857; font-size: 11px; font-weight: 700; text-transform: uppercase; padding: 2px 6px; border-radius: 4px;">Added (+{len(added)})</span>
                  <ul style="margin: 8px 0 0 0; padding-left: 20px; font-size: 13px; color: #334155;">
                    {added_items}
                  </ul>
                </div>
                """

            removed_html = ""
            if removed:
                removed_items = "".join(
                    f'<li style="margin: 4px 0;"><span style="color: #475569; text-decoration: line-through;">{html.escape(t["title"])}</span> <span style="color: #94a3b8;">— {html.escape(t["artist"])}</span></li>'
                    for t in removed[:10]
                )
                if len(removed) > 10:
                    removed_items += f'<li style="color: #64748b; font-style: italic;">...and {len(removed) - 10} more removed tracks</li>'
                removed_html = f"""
                <div style="margin-top: 10px;">
                  <span style="background-color: #f1f5f9; color: #475569; font-size: 11px; font-weight: 700; text-transform: uppercase; padding: 2px 6px; border-radius: 4px;">Removed (-{len(removed)})</span>
                  <ul style="margin: 8px 0 0 0; padding-left: 20px; font-size: 13px; color: #64748b;">
                    {removed_items}
                  </ul>
                </div>
                """

            unavail_note = (
                f'<div style="margin-top: 10px; font-size: 12px; color: #dc2626; font-weight: 600;">⚠ {unavail_count} unavailable track(s) in this playlist</div>'
                if unavail_count > 0
                else ""
            )

            modified_blocks_html.append(f"""
            <div style="background-color: #ffffff; border: 1px solid #e2e8f0; border-radius: 6px; padding: 16px; margin-bottom: 16px; box-shadow: 0 1px 2px rgba(0,0,0,0.02);">
              <div style="display: flex; justify-content: space-between; align-items: baseline; border-bottom: 1px solid #f1f5f9; padding-bottom: 10px;">
                <h3 style="margin: 0; font-size: 16px; color: #0f172a; font-weight: 600;">{html.escape(name)}</h3>
                <span style="font-size: 12px; color: #0284c7; background-color: #e0f2fe; padding: 2px 8px; border-radius: 12px; font-weight: 600;">
                  v{prev_ver} &rarr; v{ver} ({pl.get('track_count', pl.get('total_tracks', 0))} tracks)
                </span>
              </div>
              {added_html}
              {removed_html}
              {unavail_note}
            </div>
            """)

        # Alert events section
        alert_html = ""
        if new_alerts:
            alert_rows = []
            for a in new_alerts:
                alert_rows.append(f"""
                <tr>
                  <td style="padding: 8px 10px; border-bottom: 1px solid #fecdd3; font-weight: 600; color: #9f1239;">{html.escape(a.get("track_title", a.get("title", "Unknown")))}</td>
                  <td style="padding: 8px 10px; border-bottom: 1px solid #fecdd3; color: #475569;">{html.escape(a.get("artist_name", a.get("artist", "Unknown")))}</td>
                  <td style="padding: 8px 10px; border-bottom: 1px solid #fecdd3; color: #64748b;">{html.escape(a.get("playlist_name", "Unknown"))}</td>
                </tr>
                """)

            alert_html = f"""
            <div style="margin-top: 24px; background-color: #fff1f2; border: 1px solid #fecdd3; border-radius: 6px; padding: 16px;">
              <h3 style="margin: 0 0 10px 0; font-size: 15px; color: #9f1239;">Recent Delisted Track Alerts ({len(new_alerts)})</h3>
              <p style="margin: 0 0 12px 0; font-size: 13px; color: #881337;">
                The following tracks were detected as unavailable during recent scans. Use <code>tidal-cli playlist repair</code> to restore them from active catalog releases.
              </p>
              <table style="width: 100%; border-collapse: collapse; font-size: 13px; background-color: #ffffff; border-radius: 4px;">
                <thead>
                  <tr style="background-color: #ffe4e6; text-align: left; color: #9f1239;">
                    <th style="padding: 8px 10px; border-bottom: 1px solid #fecdd3;">Track Title</th>
                    <th style="padding: 8px 10px; border-bottom: 1px solid #fecdd3;">Artist</th>
                    <th style="padding: 8px 10px; border-bottom: 1px solid #fecdd3;">Playlist</th>
                  </tr>
                </thead>
                <tbody>
                  {''.join(alert_rows)}
                </tbody>
              </table>
            </div>
            """

        html_body = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>TIDAL Playlist Changes Digest</title>
</head>
<body style="margin: 0; padding: 24px 12px; background-color: #f1f5f9; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color: #1e293b;">
  <div style="max-width: 640px; margin: 0 auto; background-color: #ffffff; border-radius: 8px; border: 1px solid #cbd5e1; overflow: hidden; box-shadow: 0 2px 6px rgba(0,0,0,0.04);">
    
    <!-- Header -->
    <div style="background-color: #0f172a; padding: 24px 28px; border-bottom: 3px solid #00E8E2; color: #ffffff;">
      <h1 style="margin: 0; font-size: 20px; font-weight: 600; letter-spacing: -0.02em;">TIDAL Playlist Changes Digest</h1>
      <p style="margin: 6px 0 0 0; font-size: 13px; color: #94a3b8;">{now_str} • Activity & Availability Report</p>
    </div>

    <!-- Summary Metrics -->
    <div style="padding: 20px 28px; background-color: #f8fafc; border-bottom: 1px solid #e2e8f0;">
      <table style="width: 100%; border-collapse: collapse;">
        <tr>
          <td style="width: 25%; padding: 10px; background-color: #ffffff; border: 1px solid #e2e8f0; border-radius: 6px; text-align: center;">
            <div style="font-size: 11px; color: #64748b; font-weight: 500; text-transform: uppercase;">Modified</div>
            <div style="font-size: 20px; font-weight: 700; color: #0f172a; margin-top: 2px;">{len(modified_playlists)}</div>
          </td>
          <td style="width: 2%;"></td>
          <td style="width: 23%; padding: 10px; background-color: #ffffff; border: 1px solid #e2e8f0; border-radius: 6px; text-align: center;">
            <div style="font-size: 11px; color: #64748b; font-weight: 500; text-transform: uppercase;">Added</div>
            <div style="font-size: 20px; font-weight: 700; color: #16a34a; margin-top: 2px;">+{total_added}</div>
          </td>
          <td style="width: 2%;"></td>
          <td style="width: 23%; padding: 10px; background-color: #ffffff; border: 1px solid #e2e8f0; border-radius: 6px; text-align: center;">
            <div style="font-size: 11px; color: #64748b; font-weight: 500; text-transform: uppercase;">Removed</div>
            <div style="font-size: 20px; font-weight: 700; color: #dc2626; margin-top: 2px;">-{total_removed}</div>
          </td>
          <td style="width: 2%;"></td>
          <td style="width: 23%; padding: 10px; background-color: #ffffff; border: 1px solid #e2e8f0; border-radius: 6px; text-align: center;">
            <div style="font-size: 11px; color: #64748b; font-weight: 500; text-transform: uppercase;">Alerts</div>
            <div style="font-size: 20px; font-weight: 700; color: {'#dc2626' if total_unavail_alerts > 0 else '#16a34a'}; margin-top: 2px;">{total_unavail_alerts}</div>
          </td>
        </tr>
      </table>
    </div>

    <!-- Modified Playlists Details -->
    <div style="padding: 24px 28px; background-color: #fafafa;">
      <h2 style="margin: 0 0 16px 0; font-size: 16px; font-weight: 600; color: #0f172a;">Modified Playlists ({len(modified_playlists)})</h2>
      {''.join(modified_blocks_html)}
      {alert_html}
    </div>

    <!-- Footer -->
    <div style="background-color: #f8fafc; border-top: 1px solid #e2e8f0; padding: 16px 28px; font-size: 12px; color: #64748b; text-align: center;">
      Automated daily notification by <strong>tidal-cli</strong>.
    </div>

  </div>
</body>
</html>"""

        # Plain text version
        lines = [
            "TIDAL Playlist Changes Digest",
            "=" * 30,
            f"Date: {now_str}",
            f"Modified: {len(modified_playlists)} | Added: +{total_added} | Removed: -{total_removed} | Alerts: {total_unavail_alerts}",
            "",
            "Modified Playlists:",
            "-" * 50,
        ]

        for pl in modified_playlists:
            name = pl.get("name", pl.get("playlist_name", "Unknown"))
            ver = pl.get("version", pl.get("version_count", 1))
            prev_ver = pl.get("prev_version", max(1, ver - 1))
            diff = pl.get("diff", {}) or {}
            added = diff.get("added", [])
            removed = diff.get("removed", [])

            lines.append(f"\n* {name} (v{prev_ver} -> v{ver})")
            if added:
                lines.append(f"  Added tracks (+{len(added)}):")
                for t in added[:8]:
                    lines.append(f"    + {t['title']} - {t['artist']}")
                if len(added) > 8:
                    lines.append(f"    ...and {len(added) - 8} more added tracks")
            if removed:
                lines.append(f"  Removed tracks (-{len(removed)}):")
                for t in removed[:8]:
                    lines.append(f"    - {t['title']} - {t['artist']}")
                if len(removed) > 8:
                    lines.append(f"    ...and {len(removed) - 8} more removed tracks")

        if new_alerts:
            lines.extend([
                "",
                f"Delisted Track Alerts ({len(new_alerts)}):",
                "-" * 50,
            ])
            for a in new_alerts:
                t_name = a.get("track_title", a.get("title", "Unknown"))
                a_name = a.get("artist_name", a.get("artist", "Unknown"))
                pl_name = a.get("playlist_name", "Unknown")
                lines.append(f"- {t_name} by {a_name} (in '{pl_name}')")

        lines.extend([
            "",
            "--",
            "Automated daily notification by tidal-cli",
        ])

        return html_body, "\n".join(lines)

    # ---------------------------------------------------------------------------
    # Main Dispatcher
    # ---------------------------------------------------------------------------
    def send_digest_notification(
        self,
        batch_results: Dict[str, Any],
        force_all: bool = False,
    ) -> Dict[str, Any]:
        """Dispatch either all-playlists overview (if first run or force_all) or changes digest."""
        if not self.email_config.is_configured:
            return {"sent": False, "reason": "not_configured"}

        playlists = batch_results.get("playlists", [])
        total_alerts = batch_results.get("total_alerts", 0)
        now_date_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")

        is_first_email = not self.config.has_email_sent_before()

        # Decision 1: First email ever or force_all -> Send All Playlists Baseline Overview
        if is_first_email or force_all:
            recent_alerts = self.config.alerts_log_file  # read recent alerts if needed
            from tidal_cli.services.snapshot_service import SnapshotService
            snap_svc = SnapshotService(session=None, cfg=self.config)
            alerts = snap_svc.get_recent_alerts(limit=50)

            subject = f"[TIDAL] All Playlists Baseline Overview - {now_date_str}"
            html_body, text_body = self.render_all_playlists_report(playlists, recent_alerts=alerts)
            sent = self.send_email(subject, html_body, text_body)
            if sent:
                self.config.mark_email_sent(subject)
            return {
                "sent": sent,
                "template": "all_playlists_baseline",
                "is_first_email": is_first_email,
                "force_all": force_all,
            }

        # Decision 2: Check for playlist changes or delisted alerts
        modified = [p for p in playlists if p.get("has_changed") or p.get("status") == "created"]
        has_new_alerts = (total_alerts > 0)

        if not modified and not has_new_alerts:
            logger.info("No playlist changes or alerts detected. Email skipped.")
            return {"sent": False, "reason": "no_changes_detected"}

        # Gather recent alerts
        from tidal_cli.services.snapshot_service import SnapshotService
        snap_svc = SnapshotService(session=None, cfg=self.config)
        recent_alerts = snap_svc.get_recent_alerts(limit=20)

        subject = f"[TIDAL] Playlist Changes Digest - {now_date_str} ({len(modified)} modified, {total_alerts} alerts)"
        html_body, text_body = self.render_changes_digest(
            modified_playlists=modified,
            all_playlists_summary=playlists,
            new_alerts=recent_alerts[:total_alerts] if total_alerts > 0 else None,
        )

        sent = self.send_email(subject, html_body, text_body)
        if sent:
            self.config.mark_email_sent(subject)

        return {
            "sent": sent,
            "template": "changes_digest",
            "modified_count": len(modified),
            "alerts_count": total_alerts,
        }

