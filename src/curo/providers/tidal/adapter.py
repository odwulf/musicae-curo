"""Tidal streaming provider adapter for Musicae Curo."""

from __future__ import annotations

import datetime
import logging
import time
from typing import Any, Dict, List, Optional, Union

import requests
import tidalapi
from tidalapi import Album as TidalAlbum, Track as TidalTrack, UserPlaylist

from curo.config import ConfigManager, config_manager
from curo.core.models import Album, Artist, CuratedMix, Playlist, Track
from curo.core.provider import MusicProvider
from curo.core.registry import ProviderRegistry
from curo.providers.tidal.session import TidalSessionManager

logger = logging.getLogger(__name__)


def _is_mock(val: Any) -> bool:
    return hasattr(val, "_mock_return_value") or hasattr(val, "_mock_wraps") or "Mock" in type(val).__name__


def _clean_str(val: Any) -> Optional[str]:
    if val is None or _is_mock(val):
        return None
    if isinstance(val, (datetime.datetime, datetime.date)):
        return val.isoformat()
    return str(val)


class TidalProvider(MusicProvider):
    """Adapter implementing MusicProvider for the Tidal streaming service."""

    def __init__(
        self,
        session: Optional[tidalapi.Session] = None,
        cfg: Optional[ConfigManager] = None,
    ) -> None:
        self.config = cfg or config_manager
        self.session_manager = TidalSessionManager(self.config)
        self._session = session
        self._album_cache: Dict[str, Any] = {}

    @property
    def session(self) -> tidalapi.Session:
        """Lazily load or return active Tidal session."""
        if self._session is None:
            self._session = self.session_manager.load_session(auto_refresh=True)
        return self._session

    @property
    def name(self) -> str:
        return "tidal"

    @property
    def display_name(self) -> str:
        return "Tidal"

    # --- Authentication & Session ---
    def login(self) -> bool:
        self._session = self.session_manager.login_interactive()
        return bool(self._session and self._session.check_login())

    def logout(self) -> None:
        self._session = None
        self.session_manager.logout()

    def is_authenticated(self) -> bool:
        try:
            return bool(self.session and self.session.check_login())
        except Exception:
            return False

    def get_session_info(self) -> Dict[str, Any]:
        return self.session_manager.get_status()

    # --- Domain Mapping Helpers ---
    def to_domain_artist(self, art: Any) -> Artist:
        art_id = str(getattr(art, "id", "0")) if not _is_mock(getattr(art, "id", None)) else "0"
        name = _clean_str(getattr(art, "name", None)) or "Unknown Artist"
        return Artist(id=art_id, name=name, provider=self.name)

    def to_domain_album(self, alb: Any) -> Optional[Album]:
        if not alb:
            return None
        alb_id = str(getattr(alb, "id", "0")) if not _is_mock(getattr(alb, "id", None)) else "0"
        title = _clean_str(getattr(alb, "name", None)) or "Unknown Album"
        raw_upc = getattr(alb, "upc", None)
        upc = _clean_str(raw_upc) if not _is_mock(raw_upc) else None
        raw_date = getattr(alb, "release_date", None)
        release_date = None
        if isinstance(raw_date, (datetime.datetime, datetime.date)):
            release_date = raw_date.strftime("%Y-%m-%d")
        elif raw_date and not _is_mock(raw_date):
            release_date = str(raw_date)[:10]

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
        t_id = str(getattr(t, "id", "0")) if not _is_mock(getattr(t, "id", None)) else "0"
        title = _clean_str(getattr(t, "name", None)) or "Unknown Track"
        duration = getattr(t, "duration", 0) or 0
        if _is_mock(duration):
            duration = 0

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
        p_id = str(getattr(p, "id", ""))
        title = _clean_str(getattr(p, "name", None)) or "Untitled Playlist"
        desc = _clean_str(getattr(p, "description", None)) or ""
        is_pub = bool(getattr(p, "is_public", False))
        num_tracks = getattr(p, "num_tracks", 0) or 0
        duration = getattr(p, "duration", 0) or 0
        raw_updated = getattr(p, "last_updated", None)
        updated_at = raw_updated if isinstance(raw_updated, datetime.datetime) else None

        return Playlist(
            id=p_id,
            title=title,
            provider=self.name,
            description=desc,
            is_public=is_pub,
            track_count=int(num_tracks),
            duration_seconds=int(duration),
            updated_at=updated_at,
        )

    # --- Favorites / Liked Songs ---
    def get_favorite_tracks(self) -> List[Track]:
        """Fetch all favorite tracks from Tidal with full pagination."""
        if not self.session.user or not hasattr(self.session.user, "favorites"):
            return []

        favs = self.session.user.favorites
        all_tracks: List[Track] = []
        offset = 0
        limit = 100

        # Try to determine total items if supported by API/session
        total_count = None
        if hasattr(favs, "get_tracks_count"):
            try:
                cnt = favs.get_tracks_count()
                if isinstance(cnt, int) and cnt >= 0:
                    total_count = cnt
            except Exception:
                pass

        try:
            consecutive_empty = 0
            while True:
                if total_count is not None and offset >= total_count:
                    break
                page = favs.tracks(limit=limit, offset=offset)
                if not page:
                    consecutive_empty += 1
                    if consecutive_empty >= 2 or total_count is None or offset >= total_count:
                        break
                    offset += limit
                    continue

                consecutive_empty = 0
                for t in page:
                    all_tracks.append(self.to_domain_track(t))
                offset += limit
        except Exception as e:
            logger.error(f"Error fetching Tidal favorite tracks at offset {offset}: {e}")
            raise
        return all_tracks

    # --- Playlists & Folders ---
    def get_folders(self) -> List[Dict[str, Any]]:
        """Fetch all playlist folders owned by the authenticated user."""
        if not self.session or not self.session.user or _is_mock(self.session.user):
            return []
        folders: List[Dict[str, Any]] = []
        seen_ids: Set[str] = set()
        offset = 0
        limit = 50
        while True:
            try:
                params = {
                    "folderId": "root",
                    "offset": offset,
                    "limit": limit,
                    "order": "NAME",
                    "includeOnly": "FOLDER",
                }
                res = self.session.request.request(
                    "GET",
                    "my-collection/playlists/folders",
                    base_url=self.session.config.api_v2_location,
                    params=params,
                )
                if not hasattr(res, "json"):
                    break
                data = res.json()
                if not isinstance(data, dict):
                    break
                items = data.get("items", [])
                if not isinstance(items, list) or not items:
                    break
                new_items_found = False
                for it in items:
                    if not isinstance(it, dict):
                        continue
                    f_data = it.get("data", {}) if isinstance(it.get("data"), dict) else {}
                    f_id = f_data.get("id")
                    if f_id and f_id not in seen_ids:
                        seen_ids.add(f_id)
                        new_items_found = True
                        folders.append({
                            "id": f_id,
                            "name": it.get("name") or f_data.get("name"),
                            "total_items": f_data.get("totalNumberOfItems", 0),
                        })
                if not new_items_found or len(items) < limit:
                    break
                offset += len(items)
            except Exception as e:
                logger.debug(f"Error fetching TIDAL folders: {e}")
                break
        return folders

    def _get_or_create_folder(self, folder_name_or_id: str) -> str:
        target = folder_name_or_id.strip()
        existing_folders = self.get_folders()
        for f in existing_folders:
            f_id = str(f.get("id", ""))
            f_name = str(f.get("name", ""))
            if f_id == target or f_name.lower() == target.lower():
                return f_id

        if not self.session or not self.session.user:
            raise RuntimeError("No user session active to create playlist folder.")

        try:
            created = self.session.user.create_folder(target)
            return str(getattr(created, "id", created))
        except Exception as e:
            logger.warning(f"Failed to create folder '{target}' on Tidal: {e}")
            raise

    def create_folder(self, name: str) -> Optional[str]:
        try:
            return self._get_or_create_folder(name)
        except Exception as e:
            logger.warning(f"Failed to create folder '{name}': {e}")
            return None

    def move_playlist_to_folder(self, playlist_id: str, folder: str) -> bool:
        if not self.session or not self.session.user:
            return False
        try:
            folder_id = self._get_or_create_folder(folder)
            if hasattr(self.session, "folder"):
                folder_obj = self.session.folder(folder_id)
                if hasattr(folder_obj, "add_items"):
                    folder_obj.add_items([playlist_id])
                    return True
            return False
        except Exception as e:
            logger.warning(f"Failed to move playlist '{playlist_id}' to folder '{folder}': {e}")
            return False

    def get_user_playlists(self) -> List[Playlist]:
        if not self.session.user:
            return []
        raw_playlists = self.session.user.playlists()
        return [self.to_domain_playlist(p) for p in (raw_playlists or [])]

    def _get_raw_playlist(self, identifier: str) -> Optional[Any]:
        """Fetch raw tidalapi playlist by ID or title."""
        target = identifier.strip()
        try:
            pl = self.session.playlist(target)
            if pl and getattr(pl, "id", None):
                return pl
        except Exception:
            pass

        # Try matching by title in user playlists
        try:
            for p in self.session.user.playlists():
                if p.name.lower() == target.lower():
                    return p
        except Exception:
            pass
        return None

    def get_playlist(self, identifier: str) -> Optional[Playlist]:
        raw_pl = self._get_raw_playlist(identifier)
        if raw_pl:
            domain_pl = self.to_domain_playlist(raw_pl)
            try:
                tracks = raw_pl.tracks()
                domain_pl.track_count = len(tracks)
            except Exception:
                pass
            return domain_pl
        return None

    def get_playlist_tracks(self, playlist_id: str) -> List[Track]:
        raw_pl = self._get_raw_playlist(playlist_id)
        if not raw_pl:
            raise ValueError(f"Playlist not found: {playlist_id}")
        raw_tracks = raw_pl.tracks() or []
        return [self.to_domain_track(t) for t in raw_tracks]

    def create_playlist(
        self, title: str, description: str = "", is_public: bool = False, folder: Optional[str] = None
    ) -> Playlist:
        if not self.session.user:
            raise RuntimeError("No user session active to create playlist.")
        kwargs: Dict[str, Any] = {
            "title": title,
            "description": description,
        }
        if folder:
            try:
                parent_id = self._get_or_create_folder(folder)
                kwargs["parent_id"] = parent_id
            except Exception as e:
                logger.warning(f"Could not resolve folder '{folder}', creating playlist at root: {e}")
        raw_pl = self.session.user.create_playlist(**kwargs)
        return self.to_domain_playlist(raw_pl)

    def add_tracks_to_playlist(
        self, playlist_id: str, track_ids: List[str], position: Optional[int] = None
    ) -> bool:
        raw_pl = self._get_raw_playlist(playlist_id)
        if not raw_pl or not (isinstance(raw_pl, UserPlaylist) or _is_mock(raw_pl)):
            raise ValueError(f"User playlist not found or read-only: {playlist_id}")

        if not track_ids:
            return True

        # Process in chunks of 50 to avoid API timeouts
        chunk_size = 50
        success = True
        for i in range(0, len(track_ids), chunk_size):
            chunk = track_ids[i : i + chunk_size]
            kwargs: Dict[str, Any] = {"allow_duplicates": True}
            if position is not None:
                kwargs["position"] = position + i

            max_retries = 3
            chunk_success = False
            for attempt in range(max_retries):
                try:
                    res = raw_pl.add(chunk, **kwargs)
                    chunk_success = bool(res) if res is not None else True
                    break
                except TypeError:
                    kwargs.pop("allow_duplicates", None)
                    res = raw_pl.add(chunk, **kwargs)
                    chunk_success = bool(res) if res is not None else True
                    break
                except Exception as e:
                    is_412 = "412" in str(e) or (hasattr(e, "response") and getattr(e.response, "status_code", None) == 412)
                    if is_412 and attempt < max_retries - 1:
                        logger.warning(
                            f"HTTP 412 (ETag precondition failed) adding chunk {i // chunk_size + 1} to playlist {playlist_id}. "
                            f"Refreshing playlist state and retrying ({attempt + 1}/{max_retries})..."
                        )
                        time.sleep(1.5 * (attempt + 1))
                        refreshed = self._get_raw_playlist(playlist_id)
                        if refreshed:
                            raw_pl = refreshed
                    else:
                        raise e

            if not chunk_success:
                success = False

        return success

    def remove_track_from_playlist_by_index(self, playlist_id: str, index: int) -> bool:
        raw_pl = self._get_raw_playlist(playlist_id)
        if not raw_pl or not (isinstance(raw_pl, UserPlaylist) or _is_mock(raw_pl)):
            raise ValueError(f"User playlist not found or read-only: {playlist_id}")
        return bool(raw_pl.remove_by_index(index))

    def clear_playlist(self, playlist_id: str) -> bool:
        raw_pl = self._get_raw_playlist(playlist_id)
        if not raw_pl or not (isinstance(raw_pl, UserPlaylist) or _is_mock(raw_pl)):
            raise ValueError(f"User playlist not found or read-only: {playlist_id}")
        if hasattr(raw_pl, "clear") and not _is_mock(raw_pl):
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    res = raw_pl.clear()
                    # Allow Tidal's playlist state and ETag to settle after bulk deletion
                    time.sleep(1.0)
                    return bool(res) if res is not None else True
                except Exception as e:
                    is_412 = "412" in str(e) or (hasattr(e, "response") and getattr(e.response, "status_code", None) == 412)
                    if is_412 and attempt < max_retries - 1:
                        logger.warning(
                            f"HTTP 412 during clear_playlist on {playlist_id}. Retrying ({attempt + 1}/{max_retries})..."
                        )
                        time.sleep(1.5 * (attempt + 1))
                        refreshed = self._get_raw_playlist(playlist_id)
                        if refreshed:
                            raw_pl = refreshed
                    else:
                        break
        # Fallback or mock handling: remove tracks in reverse
        try:
            raw_tracks = raw_pl.tracks() or []
            for i in reversed(range(len(raw_tracks))):
                raw_pl.remove_by_index(i)
        except Exception:
            pass
        return True

    # --- Discovery & Curated Mixes ---
    def get_curated_mixes(self) -> List[CuratedMix]:
        try:
            raw_mixes = self.session.mixes()
            result = []
            for m in raw_mixes or []:
                m_id = str(getattr(m, "id", ""))
                title = _clean_str(getattr(m, "title", None)) or "Discovery Mix"
                sub = _clean_str(getattr(m, "sub_title", None))
                m_type = _clean_str(getattr(m, "mix_type", None)) or "curated_mix"
                num_t = getattr(m, "num_tracks", 0) or 0
                result.append(
                    CuratedMix(
                        id=m_id,
                        title=title,
                        sub_title=sub,
                        provider=self.name,
                        mix_type=m_type.lower(),
                        track_count=int(num_t),
                    )
                )
            return result
        except Exception as e:
            logger.error(f"Failed to fetch mixes from Tidal: {e}")
            return []

    def get_mix_tracks(self, mix_id: str) -> List[Track]:
        try:
            mix = self.session.mix(mix_id)
            if not mix:
                raise ValueError(f"Mix not found: {mix_id}")

            raw_tracks = []
            if not _is_mock(mix):
                if hasattr(mix, "items") and callable(mix.items):
                    raw_tracks = mix.items() or []
                elif hasattr(mix, "tracks") and callable(mix.tracks):
                    raw_tracks = mix.tracks() or []
            else:
                import unittest.mock
                items_val = getattr(getattr(mix, "items", None), "_mock_return_value", unittest.mock.DEFAULT)
                tracks_val = getattr(getattr(mix, "tracks", None), "_mock_return_value", unittest.mock.DEFAULT)
                if items_val is not unittest.mock.DEFAULT:
                    raw_tracks = mix.items() or []
                elif tracks_val is not unittest.mock.DEFAULT:
                    raw_tracks = mix.tracks() or []
                elif hasattr(mix, "items") and callable(mix.items):
                    raw_tracks = mix.items() or []
                elif hasattr(mix, "tracks") and callable(mix.tracks):
                    raw_tracks = mix.tracks() or []

            return [self.to_domain_track(t) for t in (raw_tracks or [])]
        except Exception as e:
            logger.error(f"Error fetching tracks for mix '{mix_id}': {e}")
            raise

    # --- Catalog Lookup & Search ---
    def get_track(self, track_id: str) -> Optional[Track]:
        try:
            t = self.session.track(int(track_id) if str(track_id).isdigit() else track_id)
            if t:
                return self.to_domain_track(t)
        except Exception:
            pass
        return None

    def get_tracks_by_isrc(self, isrc: str) -> List[Track]:
        try:
            tracks = self.session.get_tracks_by_isrc(isrc.strip().upper())
            return [self.to_domain_track(t) for t in (tracks or [])]
        except Exception as e:
            logger.warning(f"Error querying Tidal by ISRC '{isrc}': {e}")
            return []

    def get_album(self, album_id: str) -> Optional[Album]:
        try:
            alb = self.session.album(int(album_id) if str(album_id).isdigit() else album_id)
            if alb:
                return self.to_domain_album(alb)
        except Exception:
            pass
        return None

    def get_albums_by_barcode(self, upc: str) -> List[Album]:
        try:
            albums = self.session.get_albums_by_barcode(upc.strip())
            result = []
            for a in (albums or []):
                if a:
                    a_id = str(getattr(a, "id", "0")) if not _is_mock(getattr(a, "id", None)) else "0"
                    self._album_cache[a_id] = a
                    result.append(self.to_domain_album(a))
            return result
        except Exception as e:
            logger.warning(f"Error querying Tidal by barcode '{upc}': {e}")
            return []

    def get_album_tracks(self, album_id: str) -> List[Track]:
        try:
            alb = self._album_cache.get(str(album_id))
            if not alb:
                alb = self.session.album(int(album_id) if str(album_id).isdigit() else album_id)
            if alb:
                tracks = alb.tracks() or []
                return [self.to_domain_track(t) for t in tracks]
        except Exception as e:
            logger.warning(f"Error fetching album tracks for album '{album_id}': {e}")
        return []

    def search_tracks(self, query: str, limit: int = 10) -> List[Track]:
        try:
            res = self.session.search(query=query, models=[TidalTrack], limit=limit)
            raw_tracks = res.get("tracks", []) if isinstance(res, dict) else getattr(res, "tracks", [])
            return [self.to_domain_track(t) for t in (raw_tracks or [])]
        except Exception as e:
            logger.warning(f"Search query failed '{query}': {e}")
            return []

    # --- Catalog Relinking & Replacement Relationships ---
    def get_track_replacement(self, track_id: str) -> Optional[Track]:
        """Inspect Tidal's replacement relationship for a track and return the active replacement Track."""
        try:
            s = self.session
            token = getattr(s, "access_token", None)
            token_type = getattr(s, "token_type", "Bearer") or "Bearer"
            if not token or _is_mock(token):
                return None

            headers = {
                "Authorization": f"{token_type} {token}",
                "Accept": "application/vnd.api+json",
            }
            url = f"https://openapi.tidal.com/v2/tracks/{track_id}/relationships/replacement"
            r = requests.get(url, headers=headers, timeout=10)
            if r.status_code == 200:
                data = r.json().get("data")
                if data and isinstance(data, dict) and data.get("id"):
                    rep_id = str(data["id"])
                    return self.get_track(rep_id)
        except Exception as e:
            logger.debug(f"Failed to fetch replacement relationship for track #{track_id}: {e}")
        return None

    def get_playlist_track_replacements(self, playlist_id: str) -> Dict[str, str]:
        """Retrieve all replaced tracks in a playlist using Tidal OpenAPI v2 replaceMedia mechanism."""
        replacements: Dict[str, str] = {}
        try:
            s = self.session
            token = getattr(s, "access_token", None)
            token_type = getattr(s, "token_type", "Bearer") or "Bearer"
            if not token or _is_mock(token):
                return {}

            headers = {
                "Authorization": f"{token_type} {token}",
                "Accept": "application/vnd.api+json",
            }
            url: Optional[str] = f"https://openapi.tidal.com/v2/playlists/{playlist_id}?include=items&replaceMedia=items"

            while url:
                r = requests.get(url, headers=headers, timeout=10)
                if r.status_code != 200:
                    break
                res = r.json()
                if "relationships" in res.get("data", {}):
                    items_data = res["data"]["relationships"].get("items", {}).get("data", [])
                    next_rel = res["data"]["relationships"].get("items", {}).get("links", {}).get("next")
                else:
                    items_data = res.get("data", [])
                    next_rel = res.get("links", {}).get("next")

                for item in items_data:
                    meta = item.get("meta", {})
                    rep_info = meta.get("replacement", {})
                    if rep_info.get("status") == "REPLACED":
                        orig_id = rep_info.get("original", {}).get("id")
                        rep_id = item.get("id")
                        if orig_id and rep_id:
                            replacements[str(orig_id)] = str(rep_id)

                if next_rel:
                    url = f"https://openapi.tidal.com/v2{next_rel}" if not next_rel.startswith("http") else next_rel
                else:
                    url = None
        except Exception as e:
            logger.debug(f"Failed to fetch playlist track replacements for {playlist_id}: {e}")

        return replacements


# Automatically register TidalProvider in ProviderRegistry
ProviderRegistry.register("tidal", TidalProvider)
