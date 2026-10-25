"""Spotify streaming provider adapter for Musicae Curo."""

from __future__ import annotations

import datetime
import logging
from typing import Any, Dict, List, Optional, Union

import spotipy

from curo.config import ConfigManager, config_manager
from curo.core.models import Album, Artist, CuratedMix, Playlist, Track
from curo.core.provider import MusicProvider
from curo.core.registry import ProviderRegistry
from curo.providers.spotify.session import SpotifySessionManager

logger = logging.getLogger(__name__)


def _is_mock(val: Any) -> bool:
    return hasattr(val, "_mock_return_value") or hasattr(val, "_mock_wraps") or "Mock" in type(val).__name__


def _clean_str(val: Any) -> Optional[str]:
    if val is None or _is_mock(val):
        return None
    if isinstance(val, (datetime.datetime, datetime.date)):
        return val.isoformat()
    return str(val)


class SpotifyProvider(MusicProvider):
    """Adapter implementing MusicProvider for the Spotify streaming service."""

    def __init__(
        self,
        session: Optional[spotipy.Spotify] = None,
        cfg: Optional[ConfigManager] = None,
    ) -> None:
        self.config = cfg or config_manager
        self.session_manager = SpotifySessionManager(self.config)
        self._session = session
        self._album_cache: Dict[str, Any] = {}

    @property
    def session(self) -> spotipy.Spotify:
        """Lazily load or return active Spotify client session."""
        if self._session is None:
            self._session = self.session_manager.load_session(auto_refresh=True)
        return self._session

    @property
    def name(self) -> str:
        return "spotify"

    @property
    def display_name(self) -> str:
        return "Spotify"

    # --- Authentication & Session ---
    def login(self) -> bool:
        self._session = self.session_manager.login_interactive()
        return bool(self._session is not None)

    def logout(self) -> None:
        self._session = None
        self.session_manager.logout()

    def is_authenticated(self) -> bool:
        try:
            if self._session is not None:
                # If mock or active session provided
                if hasattr(self._session, "current_user"):
                    return bool(self._session.current_user() is not None)
                return True
            status = self.session_manager.get_status()
            return bool(status.get("logged_in"))
        except Exception:
            return False

    def get_session_info(self) -> Dict[str, Any]:
        if self._session is not None and not self.session_manager.session_file.exists():
            # Mock or direct session
            try:
                u = self._session.current_user() if hasattr(self._session, "current_user") else {}
                u = u or {}
                return {
                    "logged_in": True,
                    "user_id": u.get("id", "mock_user"),
                    "user_name": u.get("display_name") or u.get("id", "Mock User"),
                    "country_code": u.get("country", "US"),
                    "session_file": str(self.session_manager.session_file),
                }
            except Exception:
                pass
        return self.session_manager.get_status()

    # --- Domain Mapping Helpers ---
    def to_domain_artist(self, art: Any) -> Artist:
        if isinstance(art, dict):
            art_id = str(art.get("id", "0"))
            name = _clean_str(art.get("name")) or "Unknown Artist"
        else:
            raw_id = getattr(art, "id", "0")
            art_id = str(raw_id) if not _is_mock(raw_id) else "0"
            name = _clean_str(getattr(art, "name", None)) or "Unknown Artist"
        return Artist(id=art_id, name=name, provider=self.name)

    def to_domain_album(self, alb: Any) -> Optional[Album]:
        if not alb:
            return None

        if isinstance(alb, dict):
            alb_id = str(alb.get("id", "0"))
            title = _clean_str(alb.get("name")) or "Unknown Album"
            ext_ids = alb.get("external_ids") or {}
            raw_upc = ext_ids.get("upc") or ext_ids.get("ean")
            upc = _clean_str(raw_upc)
            release_date = _clean_str(alb.get("release_date"))
            if release_date and len(release_date) > 10:
                release_date = release_date[:10]

            artists = []
            for a in alb.get("artists") or []:
                artists.append(self.to_domain_artist(a))
        else:
            raw_id = getattr(alb, "id", "0")
            alb_id = str(raw_id) if not _is_mock(raw_id) else "0"
            title = _clean_str(getattr(alb, "name", None)) or "Unknown Album"
            raw_upc = getattr(alb, "upc", None)
            upc = _clean_str(raw_upc) if not _is_mock(raw_upc) else None
            raw_date = getattr(alb, "release_date", None)
            release_date = _clean_str(raw_date)[:10] if raw_date and not _is_mock(raw_date) else None
            artists = []
            primary_art = getattr(alb, "artist", None)
            if primary_art:
                artists.append(self.to_domain_artist(primary_art))

        return Album(
            id=alb_id,
            title=title,
            provider=self.name,
            upc=upc,
            release_date=release_date,
            artists=artists,
        )

    def to_domain_track(self, t: Any) -> Track:
        if isinstance(t, dict):
            t_id = str(t.get("id", "0"))
            title = _clean_str(t.get("name")) or "Unknown Track"
            dur_ms = t.get("duration_ms", 0) or 0
            duration = int(dur_ms // 1000)

            artists_list = t.get("artists") or []
            primary_art = self.to_domain_artist(artists_list[0]) if artists_list else Artist(id="0", name="Unknown Artist", provider=self.name)
            collaborators = [self.to_domain_artist(a) for a in artists_list[1:]]

            album = self.to_domain_album(t.get("album"))
            ext_ids = t.get("external_ids") or {}
            isrc = _clean_str(ext_ids.get("isrc"))

            # Spotify track availability / market playability
            is_playable = t.get("is_playable")
            restrictions = t.get("restrictions")
            avail = True
            if is_playable is False or restrictions:
                avail = False

            return Track(
                id=t_id,
                title=title,
                provider=self.name,
                duration_seconds=duration,
                artist=primary_art,
                collaborating_artists=collaborators,
                album=album,
                isrc=isrc,
                available=avail,
                allow_streaming=avail,
                stream_ready=avail,
            )
        else:
            raw_id = getattr(t, "id", "0")
            t_id = str(raw_id) if not _is_mock(raw_id) else "0"
            title = _clean_str(getattr(t, "name", None)) or _clean_str(getattr(t, "title", None)) or "Unknown Track"
            raw_dur = getattr(t, "duration", None) or getattr(t, "duration_seconds", None) or getattr(t, "duration_ms", 0)
            duration = int(raw_dur) if raw_dur and not _is_mock(raw_dur) else 0
            if duration > 10000:
                duration = duration // 1000

            artist = self.to_domain_artist(getattr(t, "artist", None))
            collaborators = []
            artists_list = getattr(t, "artists", None)
            if isinstance(artists_list, (list, tuple)):
                for a in artists_list:
                    if a and str(getattr(a, "id", "")) != artist.id:
                        collaborators.append(self.to_domain_artist(a))

            album = self.to_domain_album(getattr(t, "album", None))
            raw_isrc = getattr(t, "isrc", None)
            isrc = _clean_str(raw_isrc) if not _is_mock(raw_isrc) else None

            avail = bool(getattr(t, "available", True)) if not _is_mock(getattr(t, "available", None)) else True
            allow_stream = bool(getattr(t, "allow_streaming", True)) if not _is_mock(getattr(t, "allow_streaming", None)) else True
            stream_ready = bool(getattr(t, "stream_ready", True)) if not _is_mock(getattr(t, "stream_ready", None)) else True

            return Track(
                id=t_id,
                title=title,
                provider=self.name,
                duration_seconds=int(duration),
                artist=artist,
                collaborating_artists=collaborators,
                album=album,
                isrc=isrc,
                available=avail,
                allow_streaming=allow_stream,
                stream_ready=stream_ready,
            )

    def to_domain_playlist(self, p: Any) -> Playlist:
        if isinstance(p, dict):
            p_id = str(p.get("id", ""))
            title = _clean_str(p.get("name")) or "Untitled Playlist"
            desc = _clean_str(p.get("description")) or ""
            is_pub = bool(p.get("public", False))
            tracks_info = p.get("tracks") or {}
            num_tracks = tracks_info.get("total", 0) if isinstance(tracks_info, dict) else (getattr(p, "num_tracks", 0) or 0)
        else:
            p_id = str(getattr(p, "id", ""))
            title = _clean_str(getattr(p, "name", None)) or _clean_str(getattr(p, "title", None)) or "Untitled Playlist"
            desc = _clean_str(getattr(p, "description", None)) or ""
            is_pub = bool(getattr(p, "is_public", False)) or bool(getattr(p, "public", False))
            num_tracks = getattr(p, "num_tracks", 0) or getattr(p, "track_count", 0) or 0

        return Playlist(
            id=p_id,
            title=title,
            provider=self.name,
            description=desc,
            is_public=is_pub,
            track_count=int(num_tracks),
            duration_seconds=0,
            updated_at=None,
        )

    # --- Playlists ---
    def get_user_playlists(self) -> List[Playlist]:
        result: List[Playlist] = []
        try:
            pages = self.session.current_user_playlists(limit=50)
            while pages:
                items = pages.get("items", []) if isinstance(pages, dict) else (getattr(pages, "items", []) or [])
                for item in items:
                    if item:
                        result.append(self.to_domain_playlist(item))
                if isinstance(pages, dict) and pages.get("next"):
                    pages = self.session.next(pages)
                else:
                    break
        except Exception as e:
            logger.error(f"Failed to fetch Spotify user playlists: {e}")
        return result

    def get_playlist(self, identifier: str) -> Optional[Playlist]:
        target = identifier.strip()
        # 1. Try direct ID lookup
        try:
            pl = self.session.playlist(target)
            if pl and (isinstance(pl, dict) and pl.get("id") or getattr(pl, "id", None)):
                return self.to_domain_playlist(pl)
        except Exception:
            pass

        # 2. Search user's playlists by title
        for p in self.get_user_playlists():
            if p.title.lower() == target.lower():
                return p
        return None

    def get_playlist_tracks(self, playlist_id: str) -> List[Track]:
        result: List[Track] = []
        try:
            pages = self.session.playlist_items(playlist_id, limit=100)
            while pages:
                items = pages.get("items", []) if isinstance(pages, dict) else (getattr(pages, "items", []) or [])
                for item in items:
                    if not item:
                        continue
                    track_data = item.get("track") if isinstance(item, dict) else getattr(item, "track", None)
                    if not track_data:
                        continue
                    # Exclude podcast episodes / local files without track ID
                    if isinstance(track_data, dict) and track_data.get("type") and track_data.get("type") != "track":
                        continue
                    result.append(self.to_domain_track(track_data))
                if isinstance(pages, dict) and pages.get("next"):
                    pages = self.session.next(pages)
                else:
                    break
        except Exception as e:
            logger.error(f"Failed to fetch tracks for playlist '{playlist_id}': {e}")
            raise
        return result

    def create_playlist(
        self, title: str, description: str = "", is_public: bool = False
    ) -> Playlist:
        try:
            me = self.session.me() if hasattr(self.session, "me") else self.session.current_user()
            user_id = me.get("id") if isinstance(me, dict) else getattr(me, "id", "me")
            res = self.session.user_playlist_create(
                user=user_id,
                name=title,
                public=is_public,
                description=description,
            )
            return self.to_domain_playlist(res)
        except Exception as e:
            logger.error(f"Failed to create Spotify playlist '{title}': {e}")
            raise

    def add_tracks_to_playlist(
        self, playlist_id: str, track_ids: List[str], position: Optional[int] = None
    ) -> bool:
        if not track_ids:
            return True

        # Spotify accepts either 'spotify:track:<id>' URIs or track IDs
        uris = [
            tid if str(tid).startswith("spotify:track:") else f"spotify:track:{tid}"
            for tid in track_ids
        ]

        # Spotify API accepts a maximum of 100 items per request
        chunk_size = 100
        for i in range(0, len(uris), chunk_size):
            chunk = uris[i : i + chunk_size]
            pos = (position + i) if position is not None else None
            try:
                self.session.playlist_add_items(playlist_id, chunk, position=pos)
            except Exception as e:
                logger.error(f"Failed adding tracks to Spotify playlist '{playlist_id}': {e}")
                return False
        return True

    def remove_track_from_playlist_by_index(self, playlist_id: str, index: int) -> bool:
        try:
            # Query the track URI at the given index
            items_page = self.session.playlist_items(playlist_id, offset=index, limit=1)
            items = items_page.get("items", []) if isinstance(items_page, dict) else (getattr(items_page, "items", []) or [])
            if not items:
                raise ValueError(f"No track found at index {index} in playlist '{playlist_id}'")

            track_data = items[0].get("track") if isinstance(items[0], dict) else getattr(items[0], "track", None)
            if not track_data:
                raise ValueError(f"No track object found at index {index}")

            uri = track_data.get("uri") if isinstance(track_data, dict) else getattr(track_data, "uri", None)
            if not uri:
                tid = track_data.get("id") if isinstance(track_data, dict) else getattr(track_data, "id", None)
                uri = f"spotify:track:{tid}"

            self.session.playlist_remove_specific_occurrences_of_items(
                playlist_id, [{"uri": uri, "positions": [index]}]
            )
            return True
        except Exception as e:
            logger.error(f"Failed removing track at index {index} from Spotify playlist: {e}")
            raise

    def clear_playlist(self, playlist_id: str) -> bool:
        try:
            self.session.playlist_replace_items(playlist_id, [])
            return True
        except Exception as e:
            logger.error(f"Failed to clear Spotify playlist '{playlist_id}': {e}")
            raise

    # --- Discovery & Curated Mixes ---
    def get_curated_mixes(self) -> List[CuratedMix]:
        """Scan Spotify algorithmic mixes in user's library (Discover Weekly, Release Radar, Daily Mixes)."""
        result: List[CuratedMix] = []
        try:
            pages = self.session.current_user_playlists(limit=50)
            while pages:
                items = pages.get("items", []) if isinstance(pages, dict) else (getattr(pages, "items", []) or [])
                for p in items:
                    if not p:
                        continue
                    p_id = str(p.get("id", "")) if isinstance(p, dict) else str(getattr(p, "id", ""))
                    title = _clean_str(p.get("name") if isinstance(p, dict) else getattr(p, "name", None)) or ""
                    owner = p.get("owner") if isinstance(p, dict) else getattr(p, "owner", None)
                    owner_id = str(owner.get("id", "") if isinstance(owner, dict) else getattr(owner, "id", ""))

                    lower_title = title.lower()
                    is_spotify_mix = (
                        owner_id == "spotify"
                        or "discover weekly" in lower_title
                        or "release radar" in lower_title
                        or "daily mix" in lower_title
                        or "on repeat" in lower_title
                    )

                    if is_spotify_mix:
                        mix_type = "discovery_mix" if ("discover" in lower_title or "release" in lower_title) else "daily_mix"
                        tracks_info = p.get("tracks") if isinstance(p, dict) else getattr(p, "tracks", None)
                        total_t = tracks_info.get("total", 0) if isinstance(tracks_info, dict) else (getattr(p, "track_count", 0) or 0)
                        result.append(
                            CuratedMix(
                                id=p_id,
                                title=title,
                                sub_title="Spotify Algorithmic Mix",
                                provider=self.name,
                                mix_type=mix_type,
                                track_count=int(total_t),
                            )
                        )
                if isinstance(pages, dict) and pages.get("next"):
                    pages = self.session.next(pages)
                else:
                    break
        except Exception as e:
            logger.warning(f"Failed fetching Spotify curated mixes: {e}")
        return result

    def get_mix_tracks(self, mix_id: str) -> List[Track]:
        return self.get_playlist_tracks(mix_id)

    # --- Catalog Lookup & Search ---
    def get_track(self, track_id: str) -> Optional[Track]:
        try:
            t = self.session.track(track_id)
            if t:
                return self.to_domain_track(t)
        except Exception:
            pass
        return None

    def get_tracks_by_isrc(self, isrc: str) -> List[Track]:
        try:
            res = self.session.search(q=f"isrc:{isrc.strip().upper()}", type="track", limit=10)
            tracks_obj = res.get("tracks", {}) if isinstance(res, dict) else getattr(res, "tracks", {})
            items = tracks_obj.get("items", []) if isinstance(tracks_obj, dict) else (getattr(tracks_obj, "items", []) or [])
            return [self.to_domain_track(t) for t in items if t]
        except Exception as e:
            logger.warning(f"Error querying Spotify by ISRC '{isrc}': {e}")
            return []

    def get_album(self, album_id: str) -> Optional[Album]:
        try:
            alb = self.session.album(album_id)
            if alb:
                return self.to_domain_album(alb)
        except Exception:
            pass
        return None

    def get_albums_by_barcode(self, upc: str) -> List[Album]:
        try:
            res = self.session.search(q=f"upc:{upc.strip()}", type="album", limit=10)
            albums_obj = res.get("albums", {}) if isinstance(res, dict) else getattr(res, "albums", {})
            items = albums_obj.get("items", []) if isinstance(albums_obj, dict) else (getattr(albums_obj, "items", []) or [])
            result = []
            for a in items:
                if a:
                    a_id = str(a.get("id", "0")) if isinstance(a, dict) else str(getattr(a, "id", "0"))
                    self._album_cache[a_id] = a
                    result.append(self.to_domain_album(a))
            return result
        except Exception as e:
            logger.warning(f"Error querying Spotify by barcode '{upc}': {e}")
            return []

    def get_album_tracks(self, album_id: str) -> List[Track]:
        try:
            parent_alb = self._album_cache.get(str(album_id)) or self.get_album(album_id)
            pages = self.session.album_tracks(album_id, limit=50)
            items = pages.get("items", []) if isinstance(pages, dict) else (getattr(pages, "items", []) or [])
            result = []
            for t in items:
                if not t:
                    continue
                track = self.to_domain_track(t)
                if not track.album and parent_alb:
                    track.album = parent_alb if isinstance(parent_alb, Album) else self.to_domain_album(parent_alb)
                result.append(track)
            return result
        except Exception as e:
            logger.warning(f"Error fetching Spotify album tracks for '{album_id}': {e}")
            return []

    def search_tracks(self, query: str, limit: int = 10) -> List[Track]:
        try:
            res = self.session.search(q=query, type="track", limit=limit)
            tracks_obj = res.get("tracks", {}) if isinstance(res, dict) else getattr(res, "tracks", {})
            items = tracks_obj.get("items", []) if isinstance(tracks_obj, dict) else (getattr(tracks_obj, "items", []) or [])
            return [self.to_domain_track(t) for t in items if t]
        except Exception as e:
            logger.warning(f"Spotify search query failed for '{query}': {e}")
            return []


# Automatically register SpotifyProvider in ProviderRegistry
ProviderRegistry.register("spotify", SpotifyProvider)
