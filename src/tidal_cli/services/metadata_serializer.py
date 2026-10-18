"""Serializers for exporting comprehensive metadata of playlists, albums, and tracks to JSON."""

from __future__ import annotations

import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from tidalapi.album import Album
from tidalapi.media import Track
from tidalapi.playlist import Playlist, UserPlaylist


def _is_mock(val: Any) -> bool:
    """Detect if an object is an unconfigured unittest MagicMock."""
    return hasattr(val, "_mock_return_value") or hasattr(val, "_mock_wraps") or "Mock" in type(val).__name__


def _clean_str(val: Any) -> Optional[str]:
    """Convert value to string, handling datetime/date objects and ignoring mocks."""
    if val is None or _is_mock(val):
        return None
    if isinstance(val, (datetime.datetime, datetime.date)):
        return val.isoformat()
    if isinstance(val, Enum):
        return str(val.value)
    return str(val)


def _clean_int(val: Any, default: Optional[int] = None) -> Optional[int]:
    """Safely convert value to integer, ignoring mocks."""
    if val is None or _is_mock(val):
        return default
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def _clean_float(val: Any, default: Optional[float] = None) -> Optional[float]:
    """Safely convert value to float, ignoring mocks."""
    if val is None or _is_mock(val):
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def _clean_bool(val: Any, default: bool = False) -> bool:
    """Safely convert value to boolean, ignoring mocks."""
    if val is None or _is_mock(val):
        return default
    return bool(val)


def _clean_list(val: Any) -> List[Any]:
    """Safely return a list of clean string/primitive values."""
    if val is None or _is_mock(val):
        return []
    if not isinstance(val, (list, tuple, set)):
        return []
    cleaned = []
    for item in val:
        if _is_mock(item):
            continue
        if isinstance(item, Enum):
            cleaned.append(item.value)
        elif isinstance(item, (int, float, bool, str)):
            cleaned.append(item)
        else:
            cleaned.append(str(item))
    return cleaned


def _serialize_artist(artist: Any) -> Optional[Dict[str, Any]]:
    """Serialize an Artist object."""
    if not artist:
        return None
    a_id = _clean_int(getattr(artist, "id", None)) or _clean_str(getattr(artist, "id", None))
    a_name = _clean_str(getattr(artist, "name", None))
    if not a_id and not a_name:
        return None

    return {
        "id": a_id,
        "name": a_name or "Unknown Artist",
        "type": _clean_str(getattr(artist, "type", None)),
        "picture": _clean_str(getattr(artist, "picture", None)),
        "url": _clean_str(getattr(artist, "url", None)),
    }


def _serialize_album_summary(album: Any) -> Optional[Dict[str, Any]]:
    """Serialize a summary of an album attached to a track."""
    if not album:
        return None
    alb_id = _clean_int(getattr(album, "id", None)) or _clean_str(getattr(album, "id", None))
    alb_name = _clean_str(getattr(album, "name", None))
    if not alb_id and not alb_name:
        return None

    return {
        "id": alb_id,
        "name": alb_name or "Unknown Album",
        "release_date": _clean_str(getattr(album, "release_date", None)),
        "cover": _clean_str(getattr(album, "cover", None)),
        "video_cover": _clean_str(getattr(album, "video_cover", None)),
        "url": _clean_str(getattr(album, "listen_url", None) or getattr(album, "share_url", None)),
    }


def serialize_track(track: Track) -> Dict[str, Any]:
    """Extract all available metadata from a Track object."""
    artists_list = []
    raw_artists = getattr(track, "artists", [])
    if isinstance(raw_artists, (list, tuple)):
        for a in raw_artists:
            serialized_a = _serialize_artist(a)
            if serialized_a:
                artists_list.append(serialized_a)

    track_id = getattr(track, "id", None)
    clean_id = _clean_int(track_id) or _clean_str(track_id)
    name = _clean_str(getattr(track, "name", "")) or ""
    full_name = _clean_str(getattr(track, "full_name", None)) or name

    return {
        "id": clean_id,
        "name": name,
        "title": name,
        "full_name": full_name,
        "duration_seconds": _clean_int(getattr(track, "duration", None)),
        "track_number": _clean_int(getattr(track, "track_num", None)),
        "volume_number": _clean_int(getattr(track, "volume_num", None)),
        "index": _clean_int(getattr(track, "index", None)),
        "isrc": _clean_str(getattr(track, "isrc", None)),
        "explicit": _clean_bool(getattr(track, "explicit", False)),
        "popularity": _clean_int(getattr(track, "popularity", None)),
        "audio_quality": _clean_str(getattr(track, "audio_quality", None)),
        "audio_modes": _clean_list(getattr(track, "audio_modes", [])),
        "is_lossless": _clean_bool(getattr(track, "is_lossless", None)),
        "is_hi_res_lossless": _clean_bool(getattr(track, "is_hi_res_lossless", None)),
        "is_dolby_atmos": _clean_bool(getattr(track, "is_dolby_atmos", None)),
        "media_metadata_tags": _clean_list(getattr(track, "media_metadata_tags", [])),
        "bpm": _clean_int(getattr(track, "bpm", None)),
        "key": _clean_str(getattr(track, "key", None)),
        "key_scale": _clean_str(getattr(track, "key_scale", None)),
        "replay_gain": _clean_float(getattr(track, "replay_gain", None)),
        "peak": _clean_float(getattr(track, "peak", None)),
        "copyright": _clean_str(getattr(track, "copyright", None)),
        "allow_streaming": _clean_bool(getattr(track, "allow_streaming", None)),
        "stream_ready": _clean_bool(getattr(track, "stream_ready", None)),
        "stream_start_date": _clean_str(getattr(track, "stream_start_date", None)),
        "tidal_release_date": _clean_str(getattr(track, "tidal_release_date", None)),
        "date_added": _clean_str(getattr(track, "date_added", None)),
        "version": _clean_str(getattr(track, "version", None)),
        "listen_url": _clean_str(getattr(track, "listen_url", None)) or (f"https://tidal.com/browse/track/{clean_id}" if clean_id else None),
        "share_url": _clean_str(getattr(track, "share_url", None)),
        "artist": _serialize_artist(getattr(track, "artist", None)),
        "artists": artists_list,
        "artist_roles": _clean_str(getattr(track, "artist_roles", None)),
        "album": _serialize_album_summary(getattr(track, "album", None)),
    }


def serialize_playlist(playlist: Playlist | UserPlaylist, tracks: List[Track]) -> Dict[str, Any]:
    """Serialize full playlist metadata and all included tracks."""
    creator_obj = getattr(playlist, "creator", None)
    creator_info = None
    if creator_obj:
        c_name = _clean_str(getattr(creator_obj, "name", None))
        c_id = _clean_int(getattr(creator_obj, "id", None)) or _clean_str(getattr(creator_obj, "id", None))
        if c_name or c_id:
            creator_info = {
                "id": c_id,
                "name": c_name,
            }

    pl_id = getattr(playlist, "id", None)
    clean_id = _clean_str(pl_id) or str(pl_id)
    pl_name = _clean_str(getattr(playlist, "name", "")) or ""

    return {
        "type": "playlist",
        "id": clean_id,
        "name": pl_name,
        "title": pl_name,
        "description": _clean_str(getattr(playlist, "description", "")) or "",
        "created": _clean_str(getattr(playlist, "created", None)),
        "last_updated": _clean_str(getattr(playlist, "last_updated", None)),
        "last_item_added_at": _clean_str(getattr(playlist, "last_item_added_at", None)),
        "duration_seconds": _clean_int(getattr(playlist, "duration", None)),
        "num_tracks": len(tracks),
        "track_count": len(tracks),
        "num_videos": _clean_int(getattr(playlist, "num_videos", 0), default=0),
        "public": _clean_bool(getattr(playlist, "public", False)),
        "popularity": _clean_int(getattr(playlist, "popularity", None)),
        "creator": creator_info,
        "listen_url": _clean_str(getattr(playlist, "listen_url", None)) or (f"https://tidal.com/browse/playlist/{clean_id}" if clean_id else None),
        "share_url": _clean_str(getattr(playlist, "share_url", None)),
        "image": _clean_str(getattr(playlist, "image", None)),
        "square_picture": _clean_str(getattr(playlist, "square_picture", None)),
        "tracks": [serialize_track(t) for t in tracks],
    }


def serialize_album(album: Album, tracks: List[Track]) -> Dict[str, Any]:
    """Serialize full album metadata and all included tracks."""
    artists_list = []
    raw_artists = getattr(album, "artists", [])
    if isinstance(raw_artists, (list, tuple)):
        for a in raw_artists:
            serialized_a = _serialize_artist(a)
            if serialized_a:
                artists_list.append(serialized_a)

    alb_id = getattr(album, "id", None)
    clean_id = _clean_int(alb_id) or _clean_str(alb_id)
    alb_name = _clean_str(getattr(album, "name", "")) or ""

    return {
        "type": "album",
        "id": clean_id,
        "name": alb_name,
        "title": alb_name,
        "version": _clean_str(getattr(album, "version", None)),
        "duration_seconds": _clean_int(getattr(album, "duration", None)),
        "num_tracks": len(tracks),
        "track_count": len(tracks),
        "num_videos": _clean_int(getattr(album, "num_videos", 0), default=0),
        "num_volumes": _clean_int(getattr(album, "num_volumes", 1), default=1),
        "release_date": _clean_str(getattr(album, "release_date", None)),
        "available_release_date": _clean_str(getattr(album, "available_release_date", None)),
        "tidal_release_date": _clean_str(getattr(album, "tidal_release_date", None)),
        "year": _clean_int(getattr(album, "year", None)),
        "copyright": _clean_str(getattr(album, "copyright", None)),
        "explicit": _clean_bool(getattr(album, "explicit", False)),
        "upc": _clean_str(getattr(album, "upc", None) or getattr(album, "universal_product_number", None)),
        "popularity": _clean_int(getattr(album, "popularity", None)),
        "audio_quality": _clean_str(getattr(album, "audio_quality", None)),
        "audio_modes": _clean_list(getattr(album, "audio_modes", [])),
        "media_metadata_tags": _clean_list(getattr(album, "media_metadata_tags", [])),
        "cover": _clean_str(getattr(album, "cover", None)),
        "video_cover": _clean_str(getattr(album, "video_cover", None)),
        "artist": _serialize_artist(getattr(album, "artist", None)),
        "artists": artists_list,
        "listen_url": _clean_str(getattr(album, "listen_url", None)) or (f"https://tidal.com/browse/album/{clean_id}" if clean_id else None),
        "share_url": _clean_str(getattr(album, "share_url", None)),
        "allow_streaming": _clean_bool(getattr(album, "allow_streaming", None)),
        "tracks": [serialize_track(t) for t in tracks],
    }

