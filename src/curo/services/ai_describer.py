"""AI-powered thematic analysis and description generator for music collections."""

from dataclasses import dataclass
from typing import List, Optional, Dict, Any
import json
import logging
import re
import urllib.request
import urllib.error

from curo.core.models import Track
from curo.config import ConfigManager, AIConfig

logger = logging.getLogger(__name__)


@dataclass
class MixDescription:
    """Thematic title and rich description generated for a playlist or mix."""
    theme_title: str
    description: str


class AIDescriber:
    """Generates stylistic and thematic titles and descriptions for music track collections."""

    FEW_SHOT_PROMPT = """You are an expert music taxonomist, musicologist, and playlist curator.
Analyze the following list of tracks from a discovery mix and synthesize its cohesive musical aesthetic.

Generate two outputs:
1. "theme_title": A concise, evocative thematic title (2 to 4 genre/mood/aesthetic clusters joined by commas and '&'). Do NOT include dates, quotes, or words like 'Discovery'.
2. "description": A vivid 1-2 sentence aesthetic description detailing the musical textures, instruments, vocal styles, or lyrical themes, ending with a parenthetical list of 3-5 key representative artists featured in the mix: 'Discovery mix backup: <vivid description> (<Key Artists>).'

Style Reference Examples:
- Example 1:
  Artists: Rain McMey, Kefaya, Anakronic Electro Orkestra
  theme_title: Electro-Acoustic Protest & Klezmer Beats
  description: Discovery mix backup: Global protest lyrics, modern electronic production, and klezmer electro-grooves (Rain McMey, Kefaya, Anakronic Electro Orkestra).

- Example 2:
  Artists: Amigo the Devil, Anna Ash, Barbara Dane
  theme_title: Dark Americana & Murder Folk
  description: Discovery mix backup: Gritty gothic americana, southern murder ballads, and raw vintage folk blues (Amigo the Devil, Anna Ash, Barbara Dane).

- Example 3:
  Artists: Otyken, Ummet Ozcan, Ondar, ONUKA, Dakh Daughters
  theme_title: Siberian Shamanic Bass & Global Electro-Roots
  description: Discovery mix backup: Siberian throat singing, Tuvan jaw harps, Anatolian techno-bass, and theatrical Slavic electro-folk (Otyken, Ummet Ozcan, Ondar, ONUKA, Dakh Daughters).

- Example 4:
  Artists: Muireann Bradley, Paul Simon, Max Richter, Mick Harvey, The Smiths
  theme_title: Melancholic Chamber Folk, Baroque Pop & Jangle
  description: Discovery mix backup: Introspective acoustic fingerpicking, neo-classical chamber lullabies, and bittersweet British indie rock (Muireann Bradley, Paul Simon, Max Richter, Mick Harvey, The Smiths/Marr).

Playlist Tracks:
{track_lines}

Respond ONLY with a valid JSON object matching this schema:
{{
  "theme_title": "...",
  "description": "..."
}}
"""

    def __init__(self, cfg: Optional[ConfigManager] = None) -> None:
        self.cfg = cfg or ConfigManager()
        self.ai_config: AIConfig = self.cfg.get_ai_config()

    def is_enabled(self) -> bool:
        """Returns True if AI describer is enabled and configured with an API key."""
        return self.ai_config.is_configured

    def describe_tracks(self, tracks: List[Track], date_str: Optional[str] = None) -> Optional[MixDescription]:
        """Analyze a list of tracks and return an AI-synthesized thematic title and description."""
        if not self.is_enabled():
            logger.debug("AI describer is not configured or disabled.")
            return None
        if not tracks:
            logger.debug("Cannot describe empty track list.")
            return None

        provider = (self.ai_config.provider or "gemini").lower()
        if provider == "gemini":
            return self._describe_with_gemini(tracks, date_str)
        else:
            logger.warning(f"Unsupported AI provider: {provider}")
            return None

    def _format_track_lines(self, tracks: List[Track]) -> str:
        lines = []
        for i, t in enumerate(tracks, 1):
            artist_name = (
                t.artist.name
                if getattr(t, "artist", None) and hasattr(t.artist, "name")
                else (getattr(t, "artist_name", None) or "Unknown Artist")
            )
            album_title = (
                t.album.title
                if getattr(t, "album", None) and hasattr(t.album, "title")
                else (getattr(t, "album_title", None) or "")
            )
            album_str = f" (from '{album_title}')" if album_title else ""
            year_val = getattr(t, "year", None) or (
                t.album.release_date[:4]
                if getattr(t, "album", None) and getattr(t.album, "release_date", None)
                else None
            )
            year_str = f" [{year_val}]" if year_val else ""
            lines.append(f"{i}. {t.title} - {artist_name}{album_str}{year_str}")
        return "\n".join(lines)

    def _describe_with_gemini(self, tracks: List[Track], date_str: Optional[str] = None) -> Optional[MixDescription]:
        api_key = self.ai_config.api_key
        primary_model = self.ai_config.model or "gemini-flash-lite-latest"
        if primary_model.startswith("models/"):
            primary_model = primary_model.split("/", 1)[1]

        # Candidate model list with automatic failover for high demand (HTTP 503/429)
        models_to_try = [primary_model]
        for fallback in ("gemini-flash-lite-latest", "gemini-3.5-flash-lite", "gemini-flash-latest"):
            if fallback not in models_to_try:
                models_to_try.append(fallback)

        track_lines = self._format_track_lines(tracks)
        prompt = self.FEW_SHOT_PROMPT.format(track_lines=track_lines)

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Curo/1.0 (Universal Music Library Custodian)",
        }

        for model in models_to_try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": 0.3,
                    "response_mime_type": "application/json",
                },
            }

            try:
                data_bytes = json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(url, data=data_bytes, headers=headers, method="POST")
                with urllib.request.urlopen(req, timeout=self.ai_config.timeout) as resp:
                    resp_data = json.loads(resp.read().decode("utf-8"))

                candidates = resp_data.get("candidates") or []
                if not candidates:
                    logger.warning(f"Gemini model '{model}' returned no candidates for track description.")
                    continue

                parts = candidates[0].get("content", {}).get("parts", [])
                if not parts:
                    logger.warning(f"Gemini model '{model}' returned empty parts in response.")
                    continue

                raw_text = parts[0].get("text", "").strip()
                parsed = json.loads(raw_text)

                theme_title = str(parsed.get("theme_title") or "").strip()
                description = str(parsed.get("description") or "").strip()

                # Clean up potential leading date prefix from theme title
                theme_title = re.sub(r"^\d{4}-\d{2}-\d{2}:\s*", "", theme_title)
                # Remove any surrounding quotes
                theme_title = theme_title.strip("\"'")

                if not theme_title:
                    logger.warning(f"Gemini model '{model}' response missing theme_title.")
                    continue

                if not description.startswith("Discovery mix backup:"):
                    description = f"Discovery mix backup: {description}"

                return MixDescription(theme_title=theme_title, description=description)

            except urllib.error.HTTPError as he:
                err_msg = he.read().decode("utf-8", errors="replace") if hasattr(he, "read") else ""
                logger.warning(f"Gemini API model '{model}' HTTP {he.code} error: {err_msg[:200]}")
                if he.code in (503, 429) and model != models_to_try[-1]:
                    logger.info(f"Retrying with fallback Gemini model due to HTTP {he.code}...")
                    continue
                return None
            except Exception as e:
                logger.warning(f"Failed to generate AI track description with model '{model}': {e}")
                if model != models_to_try[-1]:
                    continue
                return None

        return None
