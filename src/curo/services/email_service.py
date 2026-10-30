"""Email notification service for playlist change digests and availability alerts."""

from __future__ import annotations

import datetime
import html
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Dict, List, Optional

from curo.config import ConfigManager, EmailConfig, config_manager

logger = logging.getLogger(__name__)


class EmailService:
    """Renders and dispatches elegant HTML/plain-text email notifications via SMTP."""

    def __init__(self, cfg: Optional[ConfigManager] = None) -> None:
        self.config = cfg or config_manager
        self.email_config = self.config.get_email_config()

    def _get_megamix_playlist_names(self) -> set[str]:
        """Collect all known megamix target playlist names from jobs configuration."""
        names = {"daily megamix", "daily fresh megamix"}
        try:
            if self.config.jobs_file.exists():
                import yaml

                with open(self.config.jobs_file, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                jobs = data.get("jobs", {})
                for job in jobs.values():
                    actions = job.get("actions", [])
                    for act in actions:
                        if act.get("action") == "create_megamix":
                            tgt = act.get("params", {}).get("target_playlist")
                            if tgt:
                                names.add(str(tgt).lower().strip())
        except Exception as e:
            logger.debug(f"Could not load jobs.yaml for megamix names: {e}")
        return names

    def is_megamix_playlist(self, playlist_name: str) -> bool:
        """Determine whether a playlist represents an automated megamix or daily mix."""
        if not playlist_name:
            return False
        lower = playlist_name.lower().strip()
        if "megamix" in lower or "daily mix" in lower:
            return True
        known_names = self._get_megamix_playlist_names()
        return lower in known_names

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

        sender = self.email_config.from_address or self.email_config.smtp_user or "curo@localhost"
        target_recipients = recipients or [r.strip() for r in self.email_config.to_address.split(",") if r.strip()]

        if not target_recipients:
            logger.warning("No valid recipient email address found.")
            return False

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = sender
        msg["To"] = ", ".join(target_recipients)
        msg["Date"] = datetime.datetime.now(datetime.timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")

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

        subject = "[Musicae Curo] Test Notification Email"
        now_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        html_body = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f8fafc; color: #1e293b; margin: 0; padding: 24px; }}
    .card {{ background-color: #ffffff; border: 1px solid #e2e8f0; border-radius: 8px; max-width: 560px; margin: 0 auto; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.05); }}
    .header {{ background-color: #0f172a; border-bottom: 3px solid #00E8E2; color: #ffffff; padding: 20px 24px; }}
    .header h1 {{ margin: 0; font-size: 18px; font-weight: 600; }}
    .content {{ padding: 24px; font-size: 14px; line-height: 1.6; }}
    .badge {{ display: inline-block; padding: 4px 10px; border-radius: 9999px; font-size: 12px; font-weight: 600; background-color: #dcfce7; color: #15803d; }}
    .footer {{ background-color: #f8fafc; border-top: 1px solid #e2e8f0; padding: 14px 24px; font-size: 12px; color: #64748b; text-align: center; }}
  </style>
</head>
<body>
  <div class="card">
    <div class="header">
      <h1>Musicae Curo • SMTP Verification</h1>
    </div>
    <div class="content">
      <p style="margin-top: 0;">Hello,</p>
      <p>This is a test notification confirming that your SMTP email settings for <strong>Musicae Curo</strong> are configured properly.</p>
      <p><span class="badge">Connection Successful</span></p>
      <ul style="color: #475569; padding-left: 20px;">
        <li>SMTP Host: <code>{self.email_config.smtp_host}</code></li>
        <li>SMTP Port: <code>{self.email_config.smtp_port}</code></li>
        <li>TLS Enabled: <code>{self.email_config.use_tls}</code></li>
        <li>Timestamp: <code>{now_str}</code></li>
      </ul>
      <p style="margin-bottom: 0;">Whenever playlists change or tracks are delisted, digests will be delivered to this address.</p>
    </div>
    <div class="footer">
      Dispatched automatically by <strong>Musicae Curo</strong>.
    </div>
  </div>
</body>
</html>"""

        text_body = f"""Musicae Curo - SMTP Verification
====================================
This is a test email confirming that your SMTP notification settings are working.

Configuration:
- SMTP Host: {self.email_config.smtp_host}
- SMTP Port: {self.email_config.smtp_port}
- TLS: {self.email_config.use_tls}
- Timestamp: {now_str}

Dispatched automatically by Musicae Curo.
"""

        try:
            sent = self.send_email(subject, html_body, text_body)
            if sent:
                self.config.mark_email_sent(subject)
            return {"success": sent, "subject": subject, "recipient": self.email_config.to_address}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def render_baseline_overview(
        self,
        playlists: List[Dict[str, Any]],
        provider_name: str = "Music Provider",
    ) -> tuple[str, str]:
        """Render the all-playlists baseline catalog template."""
        now_str = datetime.datetime.now(datetime.timezone.utc).strftime("%B %d, %Y - %H:%M UTC")

        total_playlists = len(playlists)
        total_tracks = sum(p.get("track_count", p.get("total_tracks", 0)) for p in playlists)
        total_unavail = sum(p.get("unavailable_tracks_count", p.get("unavailable_count", 0)) for p in playlists)

        rows_html = []
        delisted_items = []

        for p in playlists:
            name = html.escape(str(p.get("name", p.get("playlist_name", "Untitled"))))
            v_num = p.get("version", p.get("version_count", 1))
            t_count = p.get("track_count", p.get("total_tracks", 0))
            unavail_count = p.get("unavailable_tracks_count", p.get("unavailable_count", 0))

            if unavail_count > 0:
                badge = f'<span style="display: inline-block; padding: 2px 8px; border-radius: 9999px; font-size: 11px; font-weight: 600; background-color: #fee2e2; color: #b91c1c;">{unavail_count}</span>'
            else:
                badge = '<span style="display: inline-block; padding: 2px 8px; border-radius: 9999px; font-size: 11px; font-weight: 600; background-color: #dcfce7; color: #15803d;">0</span>'

            rows_html.append(f"""
            <tr style="border-bottom: 1px solid #f1f5f9;">
              <td style="padding: 10px 12px; font-weight: 500; color: #0f172a;">{name}</td>
              <td style="padding: 10px 12px; text-align: right; color: #64748b;">v{v_num}</td>
              <td style="padding: 10px 12px; text-align: right; color: #0f172a;">{t_count}</td>
              <td style="padding: 10px 12px; text-align: right;">{badge}</td>
            </tr>
            """)

            for un_track in p.get("unavailable_tracks", []):
                delisted_items.append({
                    "playlist": name,
                    "title": html.escape(str(un_track.get("title", un_track.get("track_title", "Unknown Title")))),
                    "artist": html.escape(str(un_track.get("artist", un_track.get("artist_name", "Unknown Artist")))),
                    "has_recovery": bool(un_track.get("historical_isrc") or un_track.get("historical_upc")),
                    "has_replacement": bool(un_track.get("has_provider_replacement") or un_track.get("provider_replacement_id")),
                    "replacement_id": un_track.get("provider_replacement_id"),
                })

        delisted_html = ""
        if delisted_items:
            items_html = []
            for d in delisted_items:
                if d.get("has_replacement"):
                    recovery_tag = '<span style="font-size: 11px; background-color: #d1fae5; color: #065f46; padding: 2px 6px; border-radius: 4px; margin-left: 6px;">Official Replacement Ready</span>'
                elif d["has_recovery"]:
                    recovery_tag = '<span style="font-size: 11px; background-color: #e0e7ff; color: #3730a3; padding: 2px 6px; border-radius: 4px; margin-left: 6px;">ISRC/UPC Cached</span>'
                else:
                    recovery_tag = '<span style="font-size: 11px; background-color: #f1f5f9; color: #64748b; padding: 2px 6px; border-radius: 4px; margin-left: 6px;">No Cache</span>'
                items_html.append(
                    f'<li style="margin-bottom: 6px; font-size: 13px;"><strong>{d["title"]}</strong> by {d["artist"]} <span style="color: #64748b;">(in <em>{d["playlist"]}</em>)</span>{recovery_tag}</li>'
                )
            delisted_html = f"""
            <div style="margin-top: 24px; padding: 16px; background-color: #fff1f2; border: 1px solid #fecdd3; border-radius: 6px;">
              <h3 style="margin: 0 0 10px 0; font-size: 14px; font-weight: 600; color: #9f1239;">Delisted Tracks Requiring Attention ({len(delisted_items)})</h3>
              <ul style="margin: 0; padding-left: 20px; color: #475569;">
                {''.join(items_html)}
              </ul>
            </div>
            """

        status_badge = (
            '<span style="display: inline-block; padding: 4px 10px; border-radius: 9999px; font-size: 12px; font-weight: 600; background-color: #fee2e2; color: #b91c1c;">Unavailable Tracks Detected</span>'
            if total_unavail > 0
            else '<span style="display: inline-block; padding: 4px 10px; border-radius: 9999px; font-size: 12px; font-weight: 600; background-color: #dcfce7; color: #15803d;">All Playlists Healthy</span>'
        )

        html_body = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Musicae Curo • Catalog Overview</title>
</head>
<body style="margin: 0; padding: 24px 12px; background-color: #f1f5f9; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color: #1e293b;">
  <div style="max-width: 640px; margin: 0 auto; background-color: #ffffff; border-radius: 8px; border: 1px solid #cbd5e1; overflow: hidden; box-shadow: 0 2px 6px rgba(0,0,0,0.04);">
    <div style="background-color: #0f172a; padding: 24px 28px; border-bottom: 3px solid #00E8E2; color: #ffffff;">
      <h1 style="margin: 0; font-size: 20px; font-weight: 600; letter-spacing: -0.02em;">Musicae Curo • {provider_name} Baseline Overview</h1>
      <p style="margin: 6px 0 0 0; font-size: 13px; color: #94a3b8;">{now_str} • Automated Snapshot Inventory</p>
    </div>
    <div style="padding: 24px 28px; background-color: #f8fafc; border-bottom: 1px solid #e2e8f0;">
      <div style="margin-bottom: 16px;">{status_badge}</div>
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
    <div style="padding: 24px 28px;">
      <h2 style="margin: 0 0 14px 0; font-size: 16px; font-weight: 600; color: #0f172a;">Personal Playlists</h2>
      <table style="width: 100%; border-collapse: collapse; font-size: 14px;">
        <thead>
          <tr style="background-color: #f1f5f9; text-align: left; color: #475569; font-size: 12px; text-transform: uppercase;">
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
    <div style="background-color: #f8fafc; border-top: 1px solid #e2e8f0; padding: 16px 28px; font-size: 12px; color: #64748b; text-align: center;">
      Versioned snapshots and historical track identifiers stored in <code>~/.config/curo/snapshots/</code>.<br>
      Automated daily notification by <strong>Musicae Curo</strong>.
    </div>
  </div>
</body>
</html>"""

        lines = [
            f"Musicae Curo • {provider_name} Baseline Overview",
            "=" * 40,
            f"Date: {now_str}",
            f"Total Playlists: {total_playlists} | Total Tracks: {total_tracks} | Unavailable: {total_unavail}",
            "",
            "Playlists:",
            "-" * 60,
            f"{'Playlist Name':<35} {'Ver':<6} {'Tracks':<8} {'Delisted':<8}",
            "-" * 60,
        ]
        for p in playlists:
            name = p.get("name", p.get("playlist_name", "Unknown"))[:33]
            v = f"v{p.get('version', p.get('version_count', 1))}"
            tc = str(p.get("track_count", p.get("total_tracks", 0)))
            un = str(p.get("unavailable_tracks_count", p.get("unavailable_count", 0)))
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
            "Automated daily notification by Musicae Curo",
        ])

        return html_body, "\n".join(lines)

    def render_changes_digest(
        self,
        modified_playlists: List[Dict[str, Any]],
        all_playlists_summary: Optional[List[Dict[str, Any]]] = None,
        new_alerts: Optional[List[Dict[str, Any]]] = None,
        provider_name: str = "Music Provider",
        follower_stats: Optional[Dict[str, Any]] = None,
    ) -> tuple[str, str]:
        """Render the email digest when one or more playlists were modified or delisted tracks detected."""
        now_str = datetime.datetime.now(datetime.timezone.utc).strftime("%B %d, %Y - %H:%M UTC")

        total_added = sum(len(p.get("track_diff", {}).get("added_tracks", [])) for p in modified_playlists)
        total_removed = sum(len(p.get("track_diff", {}).get("removed_tracks", [])) for p in modified_playlists)
        total_relinked = sum(len(p.get("track_diff", {}).get("relinked_tracks", [])) for p in modified_playlists)
        total_unavail_alerts = sum(p.get("new_alerts_count", 0) for p in modified_playlists)
        if new_alerts:
            total_unavail_alerts = max(total_unavail_alerts, len(new_alerts))

        modified_blocks_html = []
        for pl in modified_playlists:
            raw_p_name = str(pl.get("playlist_name") or pl.get("name") or "Untitled Playlist")
            p_name = html.escape(raw_p_name)
            ver = pl.get("version", 1)
            t_count = pl.get("track_count", pl.get("total_tracks", 0))
            t_diff = pl.get("track_diff", {})
            added = t_diff.get("added_tracks", [])
            removed = t_diff.get("removed_tracks", [])
            relinked = t_diff.get("relinked_tracks", [])
            reordered = t_diff.get("reordered", False)
            unavail = pl.get("unavailable_tracks", [])

            pl_provider = str(pl.get("provider") or provider_name or "Tidal").lower()
            if "tidal" in pl_provider:
                provider_display = "TIDAL"
            elif "spotify" in pl_provider:
                provider_display = "Spotify"
            elif "deezer" in pl_provider:
                provider_display = "Deezer"
            elif "qobuz" in pl_provider:
                provider_display = "Qobuz"
            elif "ytmusic" in pl_provider or "youtube" in pl_provider:
                provider_display = "YouTube Music"
            else:
                provider_display = pl_provider.capitalize()

            is_megamix = self.is_megamix_playlist(raw_p_name) and self.email_config.exclude_megamix_diff

            diff_items = []
            if is_megamix:
                relink_info = f", ↻ {len(relinked)} relinked" if relinked else ""
                diff_items.append(
                    f'<li style="color: #64748b; margin-bottom: 4px;">'
                    f'<em>Daily mix refreshed with {t_count} tracks (+{len(added)} added, -{len(removed)} removed{relink_info}). Detailed track listing omitted.</em>'
                    f'</li>'
                )
                for u in unavail:
                    u_title = html.escape(str(u.get("title", u.get("track_title", "Unknown Title"))))
                    u_art = html.escape(str(u.get("artist", u.get("artist_name", ""))))
                    diff_items.append(
                        f'<li style="color: #b91c1c; margin-bottom: 4px;">'
                        f'<strong>! Unavailable:</strong> {u_title} {f"by {u_art}" if u_art else ""}</li>'
                    )
            else:
                for t in relinked:
                    title = html.escape(str(t.get("title", "Unknown Title")))
                    art = html.escape(str(t.get("artist", "")))
                    old_id = t.get("old_id")
                    new_id = t.get("new_id")
                    id_info = f' <span style="font-size: 11px; color: #64748b;">(ID: {old_id} &rarr; {new_id})</span>' if (old_id and new_id) else ""
                    diff_items.append(
                        f'<li style="color: #0284c7; margin-bottom: 4px;">'
                        f'<strong>↻ Relinked by {provider_display}:</strong> {title}{f" by {art}" if art else ""}{id_info}</li>'
                    )
                for t in added:
                    title = html.escape(str(t.get("title", "Unknown Title")))
                    art = html.escape(str(t.get("artist", "")))
                    diff_items.append(f'<li style="color: #15803d; margin-bottom: 4px;"><strong>+ Added:</strong> {title} {f"by {art}" if art else ""}</li>')
                for t in removed:
                    title = html.escape(str(t.get("title", "Unknown Title")))
                    art = html.escape(str(t.get("artist", "")))
                    diff_items.append(f'<li style="color: #b91c1c; margin-bottom: 4px;"><strong>- Removed:</strong> {title} {f"by {art}" if art else ""}</li>')
                if reordered and not added and not removed and not relinked:
                    diff_items.append('<li style="color: #d97706; margin-bottom: 4px;"><strong>~ Reordered:</strong> Track sequence updated.</li>')
                for u in unavail:
                    u_title = html.escape(str(u.get("title", u.get("track_title", "Unknown Title"))))
                    u_art = html.escape(str(u.get("artist", u.get("artist_name", ""))))
                    diff_items.append(
                        f'<li style="color: #b91c1c; margin-bottom: 4px;">'
                        f'<strong>! Unavailable:</strong> {u_title} {f"by {u_art}" if u_art else ""}</li>'
                    )

            badge_html = ""
            if is_megamix:
                badge_html = ' <span style="font-size: 11px; background-color: #f1f5f9; color: #475569; padding: 2px 6px; border-radius: 4px; font-weight: normal;">Daily Mix</span>'

            modified_blocks_html.append(f"""
            <div style="margin-bottom: 16px; padding: 14px 16px; background-color: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px;">
              <div style="font-weight: 600; font-size: 15px; color: #0f172a; margin-bottom: 8px;">
                {p_name} <span style="font-size: 12px; color: #64748b; font-weight: normal;">(v{ver})</span>{badge_html}
              </div>
              <ul style="margin: 0; padding-left: 20px; font-size: 13px;">
                {''.join(diff_items)}
              </ul>
            </div>
            """)

        if total_relinked > 0:
            summary_table_html = f"""
      <table style="width: 100%; border-collapse: collapse;">
        <tr>
          <td style="width: 25%; padding: 10px; background-color: #ffffff; border: 1px solid #e2e8f0; border-radius: 6px; text-align: center;">
            <div style="font-size: 11px; color: #64748b; font-weight: 500; text-transform: uppercase;">Modified</div>
            <div style="font-size: 20px; font-weight: 700; color: #0f172a; margin-top: 2px;">{len(modified_playlists)}</div>
          </td>
          <td style="width: 2%;"></td>
          <td style="width: 23%; padding: 10px; background-color: #ffffff; border: 1px solid #e2e8f0; border-radius: 6px; text-align: center;">
            <div style="font-size: 11px; color: #15803d; font-weight: 500; text-transform: uppercase;">Added</div>
            <div style="font-size: 20px; font-weight: 700; color: #15803d; margin-top: 2px;">+{total_added}</div>
          </td>
          <td style="width: 2%;"></td>
          <td style="width: 23%; padding: 10px; background-color: #ffffff; border: 1px solid #e2e8f0; border-radius: 6px; text-align: center;">
            <div style="font-size: 11px; color: #b91c1c; font-weight: 500; text-transform: uppercase;">Removed</div>
            <div style="font-size: 20px; font-weight: 700; color: #b91c1c; margin-top: 2px;">-{total_removed}</div>
          </td>
          <td style="width: 2%;"></td>
          <td style="width: 23%; padding: 10px; background-color: #ffffff; border: 1px solid #e2e8f0; border-radius: 6px; text-align: center;">
            <div style="font-size: 11px; color: #0284c7; font-weight: 500; text-transform: uppercase;">Relinked</div>
            <div style="font-size: 20px; font-weight: 700; color: #0284c7; margin-top: 2px;">↻ {total_relinked}</div>
          </td>
        </tr>
      </table>"""
        else:
            summary_table_html = f"""
      <table style="width: 100%; border-collapse: collapse;">
        <tr>
          <td style="width: 33%; padding: 10px; background-color: #ffffff; border: 1px solid #e2e8f0; border-radius: 6px; text-align: center;">
            <div style="font-size: 11px; color: #64748b; font-weight: 500; text-transform: uppercase;">Modified</div>
            <div style="font-size: 20px; font-weight: 700; color: #0f172a; margin-top: 2px;">{len(modified_playlists)}</div>
          </td>
          <td style="width: 2%;"></td>
          <td style="width: 32%; padding: 10px; background-color: #ffffff; border: 1px solid #e2e8f0; border-radius: 6px; text-align: center;">
            <div style="font-size: 11px; color: #15803d; font-weight: 500; text-transform: uppercase;">Tracks Added</div>
            <div style="font-size: 20px; font-weight: 700; color: #15803d; margin-top: 2px;">+{total_added}</div>
          </td>
          <td style="width: 2%;"></td>
          <td style="width: 33%; padding: 10px; background-color: #ffffff; border: 1px solid #e2e8f0; border-radius: 6px; text-align: center;">
            <div style="font-size: 11px; color: #b91c1c; font-weight: 500; text-transform: uppercase;">Tracks Removed</div>
            <div style="font-size: 20px; font-weight: 700; color: #b91c1c; margin-top: 2px;">-{total_removed}</div>
          </td>
        </tr>
      </table>"""

        follower_html = ""
        follower_text_lines = []
        if follower_stats:
            prof = follower_stats.get("profile") or {}
            p_name = html.escape(str(prof.get("display_name") or prof.get("username") or "Profile"))
            p_curr = prof.get("current_followers", prof.get("followers_count"))
            p_delta = prof.get("delta", 0)
            pl_changes = follower_stats.get("playlist_changes") or follower_stats.get("playlists", [])
            total_pl_folls = follower_stats.get("total_playlist_followers", follower_stats.get("total_followers", 0))

            social_items = []
            if p_curr is not None:
                delta_str = ""
                if p_delta > 0:
                    delta_str = f' <span style="display: inline-block; padding: 2px 6px; border-radius: 4px; font-size: 11px; font-weight: 600; background-color: #dcfce7; color: #15803d;">+{p_delta} new</span>'
                elif p_delta < 0:
                    delta_str = f' <span style="display: inline-block; padding: 2px 6px; border-radius: 4px; font-size: 11px; font-weight: 600; background-color: #fee2e2; color: #b91c1c;">{p_delta}</span>'

                social_items.append(
                    f'<li style="margin-bottom: 6px; font-size: 13px;">'
                    f'<strong>Profile ({p_name}):</strong> {p_curr} follower{"s" if p_curr != 1 else ""}{delta_str}'
                    f'</li>'
                )
                delta_txt = f" (+{p_delta})" if p_delta > 0 else (f" ({p_delta})" if p_delta < 0 else "")
                follower_text_lines.append(f"* Profile ({prof.get('display_name') or prof.get('username') or 'Profile'}): {p_curr} followers{delta_txt}")

            if pl_changes:
                for plc in pl_changes:
                    c_name = html.escape(str(plc.get("name", "Playlist")))
                    c_curr = plc.get("current", plc.get("followers_count", 0))
                    c_delta = plc.get("delta", 0)
                    delta_badge = (
                        f' <span style="display: inline-block; padding: 2px 6px; border-radius: 4px; font-size: 11px; font-weight: 600; background-color: #dcfce7; color: #15803d;">+{c_delta} new</span>'
                        if c_delta > 0
                        else f' <span style="display: inline-block; padding: 2px 6px; border-radius: 4px; font-size: 11px; font-weight: 600; background-color: #fee2e2; color: #b91c1c;">{c_delta}</span>'
                    )
                    social_items.append(
                        f'<li style="margin-bottom: 6px; font-size: 13px;">'
                        f'Playlist <em>{c_name}</em>: {c_curr} follower{"s" if c_curr != 1 else ""}{delta_badge}'
                        f'</li>'
                    )
                    follower_text_lines.append(f"  * {plc.get('name')}: {c_curr} followers ({'+' if c_delta > 0 else ''}{c_delta})")
            elif total_pl_folls > 0:
                social_items.append(
                    f'<li style="margin-bottom: 6px; font-size: 13px; color: #64748b;">'
                    f'Total Playlist Reach: {total_pl_folls} followers across public playlists.'
                    f'</li>'
                )
                follower_text_lines.append(f"* Total Playlist Reach: {total_pl_folls} followers")

            if social_items:
                follower_html = f"""
      <div style="margin-bottom: 24px; padding: 16px 20px; background-color: #f0fdf4; border: 1px solid #bbf7d0; border-radius: 8px;">
        <h3 style="margin: 0 0 10px 0; font-size: 14px; font-weight: 600; color: #166534;">Followers & Reach</h3>
        <ul style="margin: 0; padding-left: 18px; color: #1e293b;">
          {''.join(social_items)}
        </ul>
      </div>"""

        change_details_content = ''.join(modified_blocks_html) if modified_blocks_html else '<p style="color: #64748b; font-style: italic; margin: 0;">No playlist track modifications detected in this run.</p>'

        # Render standalone new availability alerts for playlists not already in modified_playlists
        modified_ids = {str(p.get("playlist_id") or p.get("id") or "") for p in modified_playlists}
        modified_names = {str(p.get("playlist_name") or p.get("name") or "") for p in modified_playlists}
        standalone_alerts = [
            a for a in (new_alerts or [])
            if str(a.get("playlist_id") or "") not in modified_ids
            and str(a.get("playlist_name") or "") not in modified_names
        ]
        alerts_html = ""
        alerts_text_lines: List[str] = []
        if standalone_alerts:
            alert_items_html = []
            for a in standalone_alerts:
                a_title = html.escape(str(a.get("title") or a.get("track_title") or "Unknown Title"))
                a_art = html.escape(str(a.get("artist") or a.get("artist_name") or "Unknown Artist"))
                a_pl = html.escape(str(a.get("playlist_name") or "Playlist"))
                rep_id = a.get("provider_replacement_id")
                rep_badge = f' <span style="font-size: 11px; background-color: #d1fae5; color: #065f46; padding: 2px 6px; border-radius: 4px; margin-left: 6px;">Replacement Ready (ID: {html.escape(str(rep_id))})</span>' if rep_id else ""
                alert_items_html.append(
                    f'<li style="margin-bottom: 6px; font-size: 13px; color: #b91c1c;">'
                    f'<strong>! {a_title}</strong> by {a_art} <span style="color: #64748b;">(in <em>{a_pl}</em>)</span>{rep_badge}'
                    f'</li>'
                )
                rep_txt = f" [Official Replacement ID: {rep_id}]" if rep_id else ""
                alerts_text_lines.append(
                    f"  ! {a.get('title') or a.get('track_title') or 'Unknown Title'} by {a.get('artist') or a.get('artist_name') or 'Unknown Artist'} (in '{a.get('playlist_name') or 'Playlist'}'){rep_txt}"
                )
            alerts_html = f"""
      <div style="margin-top: 20px; padding: 16px 20px; background-color: #fff1f2; border: 1px solid #fecdd3; border-radius: 8px;">
        <h3 style="margin: 0 0 10px 0; font-size: 14px; font-weight: 600; color: #9f1239;">Newly Unavailable Tracks ({len(standalone_alerts)})</h3>
        <ul style="margin: 0; padding-left: 18px;">
          {''.join(alert_items_html)}
        </ul>
      </div>"""

        html_body = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Musicae Curo • Library & Social Digest</title>
</head>
<body style="margin: 0; padding: 24px 12px; background-color: #f1f5f9; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color: #1e293b;">
  <div style="max-width: 640px; margin: 0 auto; background-color: #ffffff; border-radius: 8px; border: 1px solid #cbd5e1; overflow: hidden; box-shadow: 0 2px 6px rgba(0,0,0,0.04);">
    <div style="background-color: #0f172a; padding: 24px 28px; border-bottom: 3px solid #00E8E2; color: #ffffff;">
      <h1 style="margin: 0; font-size: 20px; font-weight: 600;">Musicae Curo • Library & Social Digest</h1>
      <p style="margin: 6px 0 0 0; font-size: 13px; color: #94a3b8;">{now_str} • {len(modified_playlists)} playlist(s) modified</p>
    </div>
    <div style="padding: 20px 28px; background-color: #f8fafc; border-bottom: 1px solid #e2e8f0;">
      {summary_table_html}
    </div>
    <div style="padding: 24px 28px;">
      {follower_html}
      <h2 style="margin: 0 0 14px 0; font-size: 16px; font-weight: 600; color: #0f172a;">Playlist Change Details</h2>
      {change_details_content}
      {alerts_html}
    </div>
    <div style="background-color: #f8fafc; border-top: 1px solid #e2e8f0; padding: 16px 28px; font-size: 12px; color: #64748b; text-align: center;">
      Automated daily notification by <strong>Musicae Curo</strong>.
    </div>
  </div>
</body>
</html>"""

        relink_summary_text = f" | Relinked: {total_relinked}" if total_relinked > 0 else ""
        lines = [
            f"Musicae Curo • Library & Social Digest ({now_str})",
            "=" * 50,
            f"Modified Playlists: {len(modified_playlists)} | Added: +{total_added} | Removed: -{total_removed}{relink_summary_text}",
            "",
        ]
        if follower_text_lines:
            lines.extend([
                "Followers & Reach:",
                "-" * 40,
                *follower_text_lines,
                "",
            ])
        for pl in modified_playlists:
            raw_p_name = str(pl.get("playlist_name") or pl.get("name") or "Untitled Playlist")
            ver = pl.get("version", 1)
            t_count = pl.get("track_count", pl.get("total_tracks", 0))
            t_diff = pl.get("track_diff", {})
            added = t_diff.get("added_tracks", [])
            removed = t_diff.get("removed_tracks", [])
            relinked = t_diff.get("relinked_tracks", [])
            unavail = pl.get("unavailable_tracks", [])

            pl_provider = str(pl.get("provider") or provider_name or "Tidal").lower()
            if "tidal" in pl_provider:
                provider_display = "TIDAL"
            elif "spotify" in pl_provider:
                provider_display = "Spotify"
            elif "deezer" in pl_provider:
                provider_display = "Deezer"
            elif "qobuz" in pl_provider:
                provider_display = "Qobuz"
            elif "ytmusic" in pl_provider or "youtube" in pl_provider:
                provider_display = "YouTube Music"
            else:
                provider_display = pl_provider.capitalize()

            is_megamix = self.is_megamix_playlist(raw_p_name) and self.email_config.exclude_megamix_diff

            if is_megamix:
                relink_info = f", {len(relinked)} relinked" if relinked else ""
                lines.append(f"* {raw_p_name} (v{ver}) [Daily Mix]:")
                lines.append(f"  ~ Daily mix refreshed with {t_count} tracks (+{len(added)} added, -{len(removed)} removed{relink_info}; detailed track list omitted)")
                for u in unavail:
                    u_title = u.get("title", u.get("track_title", "Unknown Title"))
                    u_art = u.get("artist", u.get("artist_name", ""))
                    rep_id = u.get("provider_replacement_id")
                    rep_suffix = f" [Official Replacement ID: {rep_id}]" if rep_id else ""
                    lines.append(f"  ! Unavailable: {u_title} {f'by {u_art}' if u_art else ''}{rep_suffix}")
            else:
                lines.append(f"* {raw_p_name} (v{ver}):")
                for t in relinked:
                    old_id = t.get("old_id")
                    new_id = t.get("new_id")
                    id_info = f" (ID: {old_id} -> {new_id})" if (old_id and new_id) else ""
                    lines.append(f"  * Relinked by {provider_display}: {t.get('title')} by {t.get('artist')}{id_info}")
                for t in added:
                    lines.append(f"  + Added: {t.get('title')} by {t.get('artist')}")
                for t in removed:
                    lines.append(f"  - Removed: {t.get('title')} by {t.get('artist')}")
                if t_diff.get("reordered") and not added and not removed and not relinked:
                    lines.append("  ~ Reordered: Track sequence updated.")
                for u in unavail:
                    u_title = u.get("title", u.get("track_title", "Unknown Title"))
                    u_art = u.get("artist", u.get("artist_name", ""))
                    rep_id = u.get("provider_replacement_id")
                    rep_suffix = f" [Official Replacement ID: {rep_id}]" if rep_id else ""
                    lines.append(f"  ! Unavailable: {u_title} {f'by {u_art}' if u_art else ''}{rep_suffix}")
            lines.append("")

        if alerts_text_lines:
            lines.extend([
                f"Newly Unavailable Tracks ({len(alerts_text_lines)}):",
                "-" * 40,
                *alerts_text_lines,
                "",
            ])

        lines.extend([
            "--",
            "Automated daily notification by Musicae Curo",
        ])

        return html_body, "\n".join(lines)

    def send_change_digest(
        self,
        modified_playlists: List[Dict[str, Any]],
        all_playlists_summary: Optional[List[Dict[str, Any]]] = None,
        new_alerts: Optional[List[Dict[str, Any]]] = None,
        provider_name: str = "Music Provider",
        follower_stats: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Dispatch change digest email if notifications are configured."""
        if not self.email_config.is_configured:
            return {"sent": False, "reason": "Email not configured"}

        mod_count = len(modified_playlists)
        alert_count = len(new_alerts or [])
        if mod_count == 0 and follower_stats and follower_stats.get("has_changes"):
            subject = "[Musicae Curo] Social Reach: New Follower Updates"
        elif mod_count == 0 and alert_count > 0:
            subject = f"[Musicae Curo] Availability Alert: {alert_count} track(s) unavailable"
        elif mod_count > 0:
            subject = f"[Musicae Curo] Playlist Updates: {mod_count} playlist(s) modified"
        else:
            subject = "[Musicae Curo] Daily Library & Social Digest"

        html_body, text_body = self.render_changes_digest(
            modified_playlists=modified_playlists,
            all_playlists_summary=all_playlists_summary,
            new_alerts=new_alerts,
            provider_name=provider_name,
            follower_stats=follower_stats,
        )

        try:
            sent = self.send_email(subject, html_body, text_body)
            if sent:
                self.config.mark_email_sent(subject)
            return {"sent": sent, "subject": subject, "template": "changes_digest"}
        except Exception as e:
            return {"sent": False, "error": str(e)}

    def send_baseline_report(
        self,
        playlists: List[Dict[str, Any]],
        provider_name: str = "Music Provider",
    ) -> Dict[str, Any]:
        """Dispatch all-playlists baseline email."""
        if not self.email_config.is_configured:
            return {"sent": False, "reason": "Email not configured"}

        subject = f"[Musicae Curo] Catalog Baseline: {len(playlists)} playlists tracked"
        html_body, text_body = self.render_baseline_overview(playlists, provider_name=provider_name)

        try:
            sent = self.send_email(subject, html_body, text_body)
            if sent:
                self.config.mark_email_sent(subject)
            return {"sent": sent, "subject": subject, "template": "all_playlists_baseline"}
        except Exception as e:
            return {"sent": False, "error": str(e)}

    # Alias for compatibility with older test naming
    render_all_playlists_report = render_baseline_overview

    def send_digest_notification(
        self,
        batch_results: Dict[str, Any],
        force_all: bool = False,
        provider_name: str = "Music Provider",
        modified_playlists: Optional[List[Dict[str, Any]]] = None,
        new_alerts: Optional[List[Dict[str, Any]]] = None,
        follower_stats: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Dispatch either all-playlists overview (if first run or force_all) or changes digest."""
        if not self.email_config.is_configured:
            return {"sent": False, "reason": "not_configured"}

        is_first_time = not self.config.has_email_sent_before()
        all_playlists = batch_results.get("playlists", [])

        if is_first_time or force_all:
            logger.info("Dispatching full catalog baseline overview email...")
            res = self.send_baseline_report(all_playlists, provider_name=provider_name)
            res["is_first_email"] = is_first_time
            return res

        modified = modified_playlists if modified_playlists is not None else [
            p for p in all_playlists if p.get("has_changed") or p.get("snapshot_file") or p.get("status") == "created"
        ]
        alerts = new_alerts if new_alerts is not None else [
            a for p in all_playlists for a in p.get("unavailable_tracks", []) if p.get("new_alerts_count", 0) > 0
        ]
        has_follower_changes = bool(follower_stats and follower_stats.get("has_changes"))

        if not modified and not alerts and not batch_results.get("total_alerts", 0) and not has_follower_changes:
            logger.info("No playlist modifications, availability alerts, or follower changes. Skipping notification.")
            return {"sent": False, "reason": "no_changes_detected"}

        return self.send_change_digest(
            modified_playlists=modified,
            all_playlists_summary=all_playlists,
            new_alerts=alerts,
            provider_name=provider_name,
            follower_stats=follower_stats,
        )
