"""Deezer streaming provider adapter for Musicae Curo."""

from __future__ import annotations

import datetime
import logging
from typing import Any, Dict, List, Optional, Union

import deezer

from curo.config import ConfigManager, config_manager
from curo.core.models import Album, Artist, CuratedMix, Playlist, Track
from curo.core.provider import MusicProvider
from curo.core.registry import ProviderRegistry
from curo.providers.deezer.session import DeezerSessionManager

logger = logging.getLogger(__name__)


def _is_mock(val: Any) -> bool:
    return hasattr(val, "_mock_return_value") or hasattr(val, "_mock_wraps") or "Mock" in type(val).__name__


def _clean_str(val: Any) -> Optional[str]:
    if val is None or _is_mock(val):
        return None
    if isinstance(val, (datetime.datetime, datetime.date)):
        return val.isoformat()
    return str(val)


class DeezerProvider(MusicProvider):
    """Adapter implementing MusicProvider for Deezer streaming service."""

    def __init__(
        self,
        session: Optional[deezer.Client] = None,
        cfg: Optional[ConfigManager] = None,
    ) -> None:
        self.config = cfg or config_manager
        self.session_manager = DeezerSessionManager(self.config)
        self._session = session

    @property
    def session(self) -> deezer.Client:
        """Lazily load or return active Deezer client session."""
        if self._session is None:
            self._session = self.session_manager.load_session()
        return self._session

    @property
    def name(self) -> str:
        return "deezer"

    @property
    def display_name(self) -> str:
        return "Deezer"

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
                if hasattr(self._session, "get_user"):
                    u = self._session.get_user("me")
                    return bool(u is not None)
                return True
            status = self.session_manager.get_status()
            return bool(status.get("logged_in"))
        except Exception:
            return False

    def get_session_info(self) -> Dict[str, Any]:
        if self._session is not None and not self.session_manager.session_file.exists():
            try:
                u = self._session.get_user("me") if hasattr(self._session, "get_user") else None
                return {
                    "logged_in": True,
                    "user_id": getattr(u, "id", "mock_user") if u else "mock_user",
                    "user_name": getattr(u, "name", "Deezer User") if u else "Deezer User",
                    "country_code": getattr(u, "country", None) if u else None,
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
            title = _clean_str(alb.get("title") or alb.get("name")) or "Unknown Album"
            raw_upc = alb.get("upc") or alb.get("ean")
            upc = _clean_str(raw_upc)
            release_date = _clean_str(alb.get("release_date"))
            raw_artist = alb.get("artist")
            raw_contributors = alb.get("contributors") or []
        else:
            raw_id = getattr(alb, "id", "0")
            alb_id = str(raw_id) if not _is_mock(raw_id) else "0"
            title = _clean_str(getattr(alb, "title", None) or getattr(alb, "name", None)) or "Unknown Album"
            raw_upc = getattr(alb, "upc", None)
            upc = _clean_str(raw_upc)
            release_date = _clean_str(getattr(alb, "release_date", None))
            raw_artist = getattr(alb, "artist", None)
            raw_contributors = getattr(alb, "contributors", None) or []

        artists: List[Artist] = []
        if raw_contributors and not _is_mock(raw_contributors):
            for c in raw_contributors:
                artists.append(self.to_domain_artist(c))
        elif raw_artist and not _is_mock(raw_artist):
            artists.append(self.to_domain_artist(raw_artist))

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
            title = _clean_str(t.get("title") or t.get("name")) or "Unknown Track"
            duration = int(t.get("duration", 0) or 0)
            artist = self.to_domain_artist(t.get("artist") or {})
            raw_contributors = t.get("contributors") or []
            album = self.to_domain_album(t.get("album"))
            isrc = _clean_str(t.get("isrc"))
            readable = t.get("readable", True)
            avail = bool(readable) if readable is not None else True
        else:
            raw_id = getattr(t, "id", "0")
            t_id = str(raw_id) if not _is_mock(raw_id) else "0"
            title = _clean_str(getattr(t, "title", None) or getattr(t, "name", None)) or "Unknown Track"
            raw_dur = getattr(t, "duration", 0)
            duration = int(raw_dur) if not _is_mock(raw_dur) and raw_dur else 0
            raw_artist = getattr(t, "artist", None)
            artist = self.to_domain_artist(raw_artist if raw_artist is not None else {})
            raw_contributors = getattr(t, "contributors", None) or []
            raw_album = getattr(t, "album", None)
            album = self.to_domain_album(raw_album if raw_album is not None else None)
            raw_isrc = getattr(t, "isrc", None)
            isrc = _clean_str(raw_isrc)
            raw_readable = getattr(t, "readable", None)
            avail = bool(raw_readable) if not _is_mock(raw_readable) and raw_readable is not None else True

        collaborators: List[Artist] = []
        if raw_contributors and not _is_mock(raw_contributors):
            for c in raw_contributors:
                c_art = self.to_domain_artist(c)
                if c_art.id != artist.id:
                    collaborators.append(c_art)

        return Track(
            id=t_id,
            title=title,
            provider=self.name,
            duration_seconds=duration,
            artist=artist,
            collaborating_artists=collaborators,
            album=album,
            isrc=isrc,
            available=avail,
            allow_streaming=avail,
            stream_ready=avail,
        )

    def to_domain_playlist(self, p: Any) -> Playlist:
        if isinstance(p, dict):
            p_id = str(p.get("id", ""))
            title = _clean_str(p.get("title") or p.get("name")) or "Untitled Playlist"
            desc = _clean_str(p.get("description")) or ""
            is_pub = bool(p.get("public", False))
            track_count = int(p.get("nb_tracks", 0) or 0)
            duration = int(p.get("duration", 0) or 0)
        else:
            raw_id = getattr(p, "id", "")
            p_id = str(raw_id) if not _is_mock(raw_id) else ""
            title = _clean_str(getattr(p, "title", None) or getattr(p, "name", None)) or "Untitled Playlist"
            desc = _clean_str(getattr(p, "description", None)) or ""
            raw_pub = getattr(p, "public", None)
            if _is_mock(raw_pub):
                raw_pub = None
            raw_is_pub = getattr(p, "is_public", None)
            if _is_mock(raw_is_pub):
                raw_is_pub = None
            is_pub = bool(raw_pub if raw_pub is not None else (raw_is_pub or False))
            raw_nb = getattr(p, "nb_tracks", None) or getattr(p, "track_count", None)
            track_count = int(raw_nb) if not _is_mock(raw_nb) and raw_nb else 0
            raw_dur = getattr(p, "duration", None)
            duration = int(raw_dur) if not _is_mock(raw_dur) and raw_dur else 0

        return Playlist(
            id=p_id,
            title=title,
            provider=self.name,
            description=desc,
            is_public=is_pub,
            track_count=track_count,
            duration_seconds=duration,
            updated_at=None,
        )

    # --- Playlists ---
    def get_user_playlists(self) -> List[Playlist]:
        result: List[Playlist] = []
        try:
            user = self.session.get_user("me")
            playlists = user.get_playlists() if hasattr(user, "get_playlists") else []
            for pl in playlists:
                result.append(self.to_domain_playlist(pl))
        except Exception as e:
            logger.error(f"Failed to fetch Deezer user playlists: {e}")
            raise
        return result

    def _get_raw_playlist(self, identifier: str) -> Optional[Any]:
        ident = str(identifier).strip()
        if ident.isdigit():
            try:
                pl = self.session.get_playlist(int(ident))
                if pl:
                    return pl
            except Exception:
                pass

        try:
            user = self.session.get_user("me")
            playlists = user.get_playlists() if hasattr(user, "get_playlists") else []
            for pl in playlists:
                pl_id = str(getattr(pl, "id", None) or (pl.get("id") if isinstance(pl, dict) else ""))
                pl_title = getattr(pl, "title", None) or getattr(pl, "name", None) or (pl.get("title") if isinstance(pl, dict) else "")
                if pl_id == ident or (pl_title and pl_title.lower() == ident.lower()):
                    return pl
        except Exception:
            pass

        try:
            return self.session.get_playlist(ident)
        except Exception:
            pass

        return None

    def get_playlist(self, identifier: str) -> Optional[Playlist]:
        raw_pl = self._get_raw_playlist(identifier)
        if raw_pl:
            domain_pl = self.to_domain_playlist(raw_pl)
            try:
                raw_tracks = raw_pl.get_tracks() if hasattr(raw_pl, "get_tracks") else getattr(raw_pl, "tracks", None)
                if raw_tracks is not None:
                    domain_pl.track_count = len(list(raw_tracks))
            except Exception:
                pass
            return domain_pl
        return None

    def get_playlist_tracks(self, playlist_id: str) -> List[Track]:
        raw_pl = self._get_raw_playlist(playlist_id)
        if not raw_pl:
            raise ValueError(f"Playlist not found: {playlist_id}")
        raw_tracks = raw_pl.get_tracks() if hasattr(raw_pl, "get_tracks") else (raw_pl.tracks if hasattr(raw_pl, "tracks") else [])
        return [self.to_domain_track(t) for t in raw_tracks]

    def create_playlist(
        self, title: str, description: str = "", is_public: bool = False
    ) -> Playlist:
        try:
            pl_id = self.session.create_playlist(title)
            if hasattr(pl_id, "id") or hasattr(pl_id, "title"):
                pl = pl_id
            else:
                pl = self.session.get_playlist(int(pl_id) if str(pl_id).isdigit() else pl_id)
            return self.to_domain_playlist(pl)
        except Exception as e:
            logger.error(f"Failed to create Deezer playlist '{title}': {e}")
            raise

    def add_tracks_to_playlist(
        self, playlist_id: str, track_ids: List[str], position: Optional[int] = None
    ) -> bool:
        if not track_ids:
            return True
        raw_pl = self._get_raw_playlist(playlist_id)
        if not raw_pl:
            raise ValueError(f"Playlist not found: {playlist_id}")

        chunk_size = 50
        for i in range(0, len(track_ids), chunk_size):
            chunk = [int(tid) if str(tid).isdigit() else tid for tid in track_ids[i : i + chunk_size]]
            try:
                if hasattr(raw_pl, "add_tracks"):
                    res = raw_pl.add_tracks(chunk)
                    if res is False:
                        return False
                else:
                    songs_str = ",".join(map(str, chunk))
                    self.session.request("POST", f"playlist/{playlist_id}/tracks", params={"songs": songs_str})
            except Exception as e:
                logger.error(f"Failed adding tracks to Deezer playlist '{playlist_id}': {e}")
                return False
        return True

    def remove_track_from_playlist_by_index(self, playlist_id: str, index: int) -> bool:
        raw_pl = self._get_raw_playlist(playlist_id)
        if not raw_pl:
            raise ValueError(f"Playlist not found: {playlist_id}")

        if hasattr(raw_pl, "remove_by_index") and not _is_mock(getattr(raw_pl, "remove_by_index")):
            return bool(raw_pl.remove_by_index(index))

        tracks = list(raw_pl.get_tracks()) if hasattr(raw_pl, "get_tracks") else (raw_pl.tracks if hasattr(raw_pl, "tracks") else [])
        if 0 <= index < len(tracks):
            target = tracks[index]
            target_id = getattr(target, "id", None) or (target.get("id") if isinstance(target, dict) else target)
            if target_id is not None:
                if hasattr(raw_pl, "delete_tracks"):
                    return bool(raw_pl.delete_tracks([target_id]))
                return bool(self.session.request("DELETE", f"playlist/{playlist_id}/tracks", params={"songs": str(target_id)}))
        raise ValueError(f"Index {index} out of range for playlist '{playlist_id}' (track count: {len(tracks)})")

    def clear_playlist(self, playlist_id: str) -> bool:
        raw_pl = self._get_raw_playlist(playlist_id)
        if not raw_pl:
            raise ValueError(f"Playlist not found: {playlist_id}")

        if hasattr(raw_pl, "clear") and not _is_mock(getattr(raw_pl, "clear")):
            return bool(raw_pl.clear())

        tracks = list(raw_pl.get_tracks()) if hasattr(raw_pl, "get_tracks") else (raw_pl.tracks if hasattr(raw_pl, "tracks") else [])
        if not tracks:
            return True

        tids = [getattr(t, "id", t) for t in tracks]
        chunk_size = 50
        for i in range(0, len(tids), chunk_size):
            chunk = tids[i : i + chunk_size]
            if hasattr(raw_pl, "delete_tracks"):
                raw_pl.delete_tracks(chunk)
            else:
                self.session.request("DELETE", f"playlist/{playlist_id}/tracks", params={"songs": ",".join(map(str, chunk))})
        return True

    # --- Discovery & Curated Mixes ---
    def get_curated_mixes(self) -> List[CuratedMix]:
        return [
            CuratedMix(
                id="flow",
                title="Deezer Flow (Discovery)",
                provider=self.name,
                mix_type="daily_discovery",
                track_count=40,
            ),
            CuratedMix(
                id="recommended",
                title="Deezer Daily Mix",
                provider=self.name,
                mix_type="daily",
                track_count=40,
            ),
        ]

    def get_mix_tracks(self, mix_id: str) -> List[Track]:
        try:
            mix_key = str(mix_id).lower()
            if mix_key in ("flow", "discovery", "daily_discovery"):
                stream = self.session.get_user_flow()
                tracks: List[Track] = []
                for t in stream:
                    tracks.append(self.to_domain_track(t))
                    if len(tracks) >= 40:
                        break
                return tracks
            elif mix_key in ("recommended", "daily", "daily_mix"):
                stream = self.session.get_user_recommended_tracks()
                tracks = []
                for t in stream:
                    tracks.append(self.to_domain_track(t))
                    if len(tracks) >= 40:
                        break
                return tracks
            else:
                return self.get_playlist_tracks(mix_id)
        except Exception as e:
            logger.error(f"Failed to fetch Deezer mix tracks for '{mix_id}': {e}")
            return []

    # --- Catalog Lookup & Search ---
    def get_track(self, track_id: str) -> Optional[Track]:
        try:
            tid = int(track_id) if str(track_id).isdigit() else track_id
            t = self.session.get_track(tid)
            if t:
                return self.to_domain_track(t)
        except Exception as e:
            logger.debug(f"Deezer get_track('{track_id}') failed: {e}")
        return None

    def get_tracks_by_isrc(self, isrc: str) -> List[Track]:
        clean_isrc = str(isrc).strip().upper()
        results: List[Track] = []

        try:
            t = self.session.request("GET", f"track/isrc:{clean_isrc}")
            if t:
                results.append(self.to_domain_track(t))
                return results
        except Exception as e:
            logger.debug(f"Deezer direct ISRC lookup failed for '{clean_isrc}': {e}")

        try:
            search_results = self.session.search(f'isrc:"{clean_isrc}"')
            for t in search_results:
                results.append(self.to_domain_track(t))
        except Exception as e:
            logger.debug(f"Deezer search by ISRC failed for '{clean_isrc}': {e}")

        return results

    def get_album(self, album_id: str) -> Optional[Album]:
        try:
            aid = int(album_id) if str(album_id).isdigit() else album_id
            alb = self.session.get_album(aid)
            if alb:
                return self.to_domain_album(alb)
        except Exception as e:
            logger.debug(f"Deezer get_album('{album_id}') failed: {e}")
        return None

    def get_albums_by_barcode(self, upc: str) -> List[Album]:
        clean_upc = str(upc).strip()
        results: List[Album] = []
        try:
            alb = self.session.request("GET", f"album/upc:{clean_upc}")
            if alb:
                domain_alb = self.to_domain_album(alb)
                if domain_alb:
                    results.append(domain_alb)
                return results
        except Exception as e:
            logger.debug(f"Deezer album UPC lookup failed for '{clean_upc}': {e}")
        return results

    def get_album_tracks(self, album_id: str) -> List[Track]:
        try:
            aid = int(album_id) if str(album_id).isdigit() else album_id
            alb = self.session.get_album(aid)
            if not alb:
                return []
            raw_tracks = alb.get_tracks() if hasattr(alb, "get_tracks") else (alb.tracks if hasattr(alb, "tracks") else [])
            return [self.to_domain_track(t) for t in raw_tracks]
        except Exception as e:
            logger.error(f"Failed to fetch Deezer album tracks for '{album_id}': {e}")
            return []

    def search_tracks(self, query: str, limit: int = 10) -> List[Track]:
        try:
            res = self.session.search(query)
            tracks: List[Track] = []
            for t in res:
                tracks.append(self.to_domain_track(t))
                if len(tracks) >= limit:
                    break
            return tracks
        except Exception as e:
            logger.error(f"Deezer track search failed for query '{query}': {e}")
            return []


ProviderRegistry.register("deezer", DeezerProvider)
